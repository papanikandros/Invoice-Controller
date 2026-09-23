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

    def test_raised_error_audits_traceback(self, tmp_path: Path, capsys) -> None:
        job = JobStore(tmp_path).create("p", "t")
        def failing_helper():
            raise ZeroDivisionError("boom")
        try:
            failing_helper()
        except ZeroDivisionError as exc:
            job.add_error(exc)
        job.add_error(RuntimeError("nie geworfen"), filename="a.pdf")

        errors = [e for e in map(json.loads, (job.run_dir / "audit.jsonl").read_text().splitlines())
                  if e["event"] == "error"]
        assert "failing_helper" in errors[0]["traceback"]
        assert "traceback" not in errors[1]
        assert "failing_helper" in capsys.readouterr().err

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
        job.log("Rechnung: a.pdf")
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
        # Protokoll survives the restart (empty-Protokoll bug, 2026-09-03)
        assert any("Rechnung: a.pdf" in line for line in loaded.progress)
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


class TestVneConfigFromParams:
    def test_full_params(self) -> None:
        from decimal import Decimal

        from invoice_controller.web.registry import vne_config_from_params

        config, flags = vne_config_from_params({
            "kunde_name": "ZePa GmbH", "kunde_adresse": "Weg 1, 12345 Ort",
            "zeitraum_von": "01.01.2025", "zeitraum_bis": "31.12.2026",
            "foerderbetrag": "45.000,00", "foerderanteil": "40", "agvo": "",
        })
        assert flags == []
        assert config.client.name == "ZePa GmbH"
        assert config.has_window and config.window_ok(__import__("datetime").date(2025, 6, 1))
        assert config.bescheid.foerderbetrag == Decimal("45000.00")
        assert config.bescheid.kostendeckel_foerderanteil == Decimal("0.4")

    def test_empty_params_degrade_with_named_flags(self) -> None:
        from invoice_controller.web.registry import vne_config_from_params

        config, flags = vne_config_from_params({})
        assert config.client is None and not config.has_window and config.bescheid is None
        assert any("Adressprüfung" in f for f in flags)
        assert any("Zeitraum" in f for f in flags)
        assert any("Förderbetrag-Block" in f for f in flags)

    def test_unparseable_date_names_the_field(self) -> None:
        import pytest

        from invoice_controller.web.registry import vne_config_from_params

        with pytest.raises(ValueError, match="Bewilligungszeitraum von.*TT.MM.JJJJ"):
            vne_config_from_params({"zeitraum_von": "nächstes Jahr"})


class TestBescheidMerge:
    def test_ui_fields_win_and_gaps_fill(self) -> None:
        from datetime import date
        from decimal import Decimal

        from invoice_controller.extract.bescheid import EewBescheidMeta
        from invoice_controller.web.registry import (
            merge_bescheid_into_config,
            vne_config_from_params,
        )

        config, _ = vne_config_from_params({"foerderbetrag": "50.000,00"})
        meta = EewBescheidMeta(
            empfaenger_name="Wirox GmbH",
            bewilligungszeitraum_start=date(2025, 1, 1),
            bewilligungszeitraum_end=date(2026, 12, 31),
            foerderbetrag=Decimal("45000"),
            foerderanteil_pct=Decimal("40"),
        )
        adopted = merge_bescheid_into_config(config, meta)
        assert config.client.name == "Wirox GmbH"
        assert config.has_window
        # UI-typed Förderbetrag beats the extracted one:
        assert config.bescheid.foerderbetrag == Decimal("50000.00")
        assert config.bescheid.kostendeckel_foerderanteil == Decimal("0.4")
        assert any("Kunde" in a for a in adopted)
        assert not any("Förderbetrag " in a for a in adopted)

    def test_empty_meta_adopts_nothing(self) -> None:
        from invoice_controller.extract.bescheid import EewBescheidMeta
        from invoice_controller.web.registry import (
            merge_bescheid_into_config,
            vne_config_from_params,
        )

        config, _ = vne_config_from_params({})
        assert merge_bescheid_into_config(config, EewBescheidMeta()) == []
        assert config.client is None and not config.has_window


class TestProgramRegistry:
    def test_every_procedure_belongs_to_exactly_one_program(self) -> None:
        """The start page offers the program first (2026-09-24); a procedure without
        a program would be unreachable, one in two programs would run twice."""
        from invoice_controller.web.registry import (
            PROCEDURES,
            PROGRAMS,
            procedures_for,
            program_by_key,
        )

        seen = [p.key for prog in PROGRAMS for p in procedures_for(prog)]
        assert sorted(seen) == sorted(p.key for p in PROCEDURES)
        assert len(seen) == len(set(seen))
        for prog in PROGRAMS:
            assert program_by_key(prog.key) is prog
            assert all(p.key.startswith(prog.key + "-") for p in procedures_for(prog))
        assert program_by_key("laeufe") is None          # the history route must not be a program
