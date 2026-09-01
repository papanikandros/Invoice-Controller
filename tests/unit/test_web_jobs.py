"""U1 — error mapper, job lifecycle, audit log, retention, rehydration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from invoice_controller.web.errors import describe_error
from invoice_controller.web.jobs import RETAIN_RUNS, JobStore


class TestErrorMapper:
    def test_rate_limit_maps_to_german_message(self) -> None:
        class ModelHTTPError(Exception):
            status_code = 429

        err = describe_error(ModelHTTPError("boom"), filename="a.pdf")
        assert err.error_class == "llm-rate-limit"
        assert err.message_de.startswith("a.pdf: ")
        assert "Rate-Limit" in err.message_de

    def test_broken_pdf(self) -> None:
        class PdfReadError(Exception):
            pass

        err = describe_error(PdfReadError("EOF marker not found"))
        assert err.error_class == "bad-pdf"
        assert "passwortgeschützt" in err.message_de

    def test_missing_provider_key(self) -> None:
        err = describe_error(RuntimeError("No LLM provider configured. Add OPEN_ROUTER_API_KEY"))
        assert err.error_class == "llm-not-configured"

    def test_unexpected_keeps_type_in_detail_not_message(self) -> None:
        err = describe_error(ZeroDivisionError("division by zero"))
        assert err.error_class == "unexpected"
        assert "division by zero" not in err.message_de
        assert "division by zero" in err.detail


class TestJobStore:
    def test_run_dir_uploads_and_audit(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        job = store.create("eew-cost-estimation", "Test")
        store.save_upload(job, "../../evil/Angebot.pdf", b"x", subfolder=None)
        assert (job.run_dir / "inputs" / "Angebot.pdf").exists(), "path traversal sanitized"
        store.save_upload(job, "proof.png", b"y", subfolder="Zahlungsnachweise")
        assert (job.run_dir / "inputs" / "Zahlungsnachweise" / "proof.png").exists()
        events = [json.loads(l)["event"] for l in (job.run_dir / "audit.jsonl").read_text().splitlines()]
        assert events[0] == "created" and "upload" in events

    def test_lifecycle_success_and_error(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)

        async def scenario() -> None:
            ok = store.create("p", "ok")
            def runner_ok(j):
                j.log("работает")
                out = j.run_dir / "out.xlsx"; out.write_bytes(b"x"); j.add_output(out)
            store.start(ok, runner_ok)

            bad = store.create("p", "bad")
            def runner_bad(j):
                raise ValueError("Keine PDF-Dateien hochgeladen.")
            store.start(bad, runner_bad)
            await asyncio.gather(ok._task, bad._task)
            assert ok.status == "fertig" and ok.outputs
            assert bad.status == "fehler"
            assert bad.errors and bad.errors[0].error_class == "project-input"
            assert "hochgeladen" in bad.errors[0].message_de

        asyncio.run(scenario())

    def test_retention_prunes_old_runs(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        for i in range(RETAIN_RUNS + 5):
            job = store.create("p", f"run {i}")
            job.status = "fertig"
        dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(dirs) <= RETAIN_RUNS + 1  # +1: the newest creation prunes BEFORE itself exists

    def test_rehydration_after_restart(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        job = store.create("beg-vne-generation", "Projekt X")
        job.file_status("a.pdf", "ok")
        job.add_flag("kein Zahlungsnachweis")
        out = job.run_dir / "Kostenzusammenstellung.xlsx"; out.write_bytes(b"x")
        job.add_output(out)
        job.status = "fertig"
        job._audit("finished", status="fertig")

        fresh = JobStore(tmp_path)
        fresh.load_history()
        loaded = fresh.jobs[job.id]
        assert loaded.title == "Projekt X"
        assert loaded.procedure == "beg-vne-generation"
        assert loaded.files["a.pdf"].status == "ok"
        assert loaded.flags == ["kein Zahlungsnachweis"]
        assert loaded.outputs == [out]

    def test_interrupted_run_marked_failed_on_rehydration(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path)
        job = store.create("p", "crashed")   # no 'finished' event written
        fresh = JobStore(tmp_path)
        fresh.load_history()
        loaded = fresh.jobs[job.id]
        assert loaded.status == "fehler"
        assert any(e.error_class == "server-restart" for e in loaded.errors)
