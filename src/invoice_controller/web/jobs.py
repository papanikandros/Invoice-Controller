"""Run lifecycle for the web UI: per-run directory, background execution, live
progress, structured audit log, retention of the last N runs.

Every run gets `tmp/webruns/<job-id>/` with `inputs/` (the uploads, payment proofs
routed into an internal `Zahlungsnachweise/` subfolder so the shared classifier's
parent-folder tier works without the colleague knowing about it), the produced
outputs, and `audit.jsonl` — one JSON line per event (upload, start, progress,
per-file status, flag, error, done). The audit log is the machine-readable trail
the U1 contract requires; the UI renders the same events live.

Concurrency is deliberately unbounded (user decision 2026-09-01) — each run is an
independent asyncio task executing the blocking pipeline in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from invoice_controller.web.errors import UserError, describe_error

RETAIN_RUNS = 20


@dataclass
class FileStatus:
    name: str
    status: str = "wartet"      # wartet | läuft | ok | hinweis | fehler
    message: str = ""


@dataclass
class Job:
    id: str
    procedure: str
    title: str
    run_dir: Path
    created: datetime = field(default_factory=datetime.now)
    status: str = "läuft"       # läuft | fertig | fehler | abgebrochen
    progress: list[str] = field(default_factory=list)
    files: dict[str, FileStatus] = field(default_factory=dict)
    outputs: list[Path] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)      # review flags (not errors)
    errors: list[UserError] = field(default_factory=list)
    finished: datetime | None = None
    # Bumped on every event — the job page refreshes ONLY when this changes, so an
    # idle page never re-renders (re-rendering collapses expansions and kills text
    # selection; fixed 2026-09-03).
    version: int = 0
    _task: asyncio.Task | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- event API (thread-safe: pipelines run in a worker thread) --------------------

    def _audit(self, event: str, **data: Any) -> None:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "job": self.id, "procedure": self.procedure, "event": event, **data}
        with self._lock:
            self.version += 1
            with (self.run_dir / "audit.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def log(self, message: str) -> None:
        with self._lock:
            self.progress.append(f"{datetime.now():%H:%M:%S} {message}")
            if len(self.progress) > 500:
                del self.progress[:100]
        self._audit("progress", message=message)

    def file_status(self, name: str, status: str, message: str = "") -> None:
        with self._lock:
            self.files[name] = FileStatus(name=name, status=status, message=message)
        self._audit("file", file=name, status=status, message=message)

    def add_flag(self, message: str) -> None:
        with self._lock:
            self.flags.append(message)
        self._audit("flag", message=message)

    def add_error(self, exc: BaseException, *, filename: str | None = None) -> UserError:
        err = describe_error(exc, filename=filename)
        with self._lock:
            self.errors.append(err)
        self._audit("error", error_class=err.error_class, message=err.message_de, detail=err.detail)
        if filename:
            self.file_status(filename, "fehler", err.message_de)
        return err

    def add_output(self, path: Path) -> None:
        with self._lock:
            self.outputs.append(path)
        self._audit("output", file=str(path))

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()


class JobStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}

    def create(self, procedure: str, title: str) -> Job:
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        run_dir = self.base_dir / job_id
        (run_dir / "inputs").mkdir(parents=True)
        job = Job(id=job_id, procedure=procedure, title=title, run_dir=run_dir)
        self.jobs[job_id] = job
        job._audit("created", title=title)
        self._prune()
        return job

    def save_upload(self, job: Job, filename: str, content: bytes, *, subfolder: str | None = None) -> Path:
        """Uploads are sanitized to their basename; `subfolder` reconstructs the
        classifier signals (e.g. Zahlungsnachweise/) server-side."""
        safe = Path(filename).name
        target_dir = job.run_dir / "inputs" / subfolder if subfolder else job.run_dir / "inputs"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe
        target.write_bytes(content)
        job._audit("upload", file=safe, bytes=len(content), subfolder=subfolder)
        job.file_status(safe, "wartet")
        return target

    def start(self, job: Job, runner: Callable[[Job], None]) -> None:
        """Run the (blocking) procedure in a worker thread; lifecycle + error
        contract handled here so every runner stays simple."""

        async def _run() -> None:
            try:
                await asyncio.to_thread(runner, job)
                job.status = "fehler" if job.status == "läuft" and not job.outputs else "fertig"
                if job.errors and job.outputs:
                    job.status = "fertig"   # partial success: outputs exist, errors listed
            except asyncio.CancelledError:
                job.status = "abgebrochen"
                job.add_error(asyncio.CancelledError())
            except BaseException as exc:  # noqa: BLE001 — the UI must show SOMETHING
                job.status = "fehler"
                job.add_error(exc)
            finally:
                job.finished = datetime.now()
                job._audit("finished", status=job.status, outputs=[str(p) for p in job.outputs])

        job._task = asyncio.get_event_loop().create_task(_run())

    def _prune(self) -> None:
        """Keep the newest RETAIN_RUNS run directories (user decision 2026-09-01)."""
        dirs = sorted((d for d in self.base_dir.iterdir() if d.is_dir()), key=lambda d: d.name)
        for stale in dirs[:-RETAIN_RUNS]:
            active = self.jobs.get(stale.name)
            if active is not None and active.status == "läuft":
                continue
            shutil.rmtree(stale, ignore_errors=True)
            self.jobs.pop(stale.name, None)

    def load_history(self) -> None:
        """Rehydrate finished runs from disk after a server restart (audit.jsonl is
        the source of truth; only what the history view needs is recovered)."""
        for run_dir in sorted((d for d in self.base_dir.iterdir() if d.is_dir()), key=lambda d: d.name):
            if run_dir.name in self.jobs:
                continue
            audit = run_dir / "audit.jsonl"
            if not audit.exists():
                continue
            # Status starts as "läuft"; only an audited 'finished' event upgrades it —
            # a run the server died on is thereby detected below.
            job = Job(id=run_dir.name, procedure="?", title=run_dir.name,
                      run_dir=run_dir, status="läuft")
            try:
                for line in audit.read_text(encoding="utf-8").splitlines():
                    entry = json.loads(line)
                    job.procedure = entry.get("procedure", job.procedure)
                    if entry["event"] == "created":
                        job.title = entry.get("title", job.title)
                        job.created = datetime.fromisoformat(entry["ts"])
                    elif entry["event"] == "progress":
                        # HH:MM:SS prefix mirrors the live log format.
                        stamp = entry["ts"][11:19] if len(entry["ts"]) >= 19 else ""
                        job.progress.append(f"{stamp} {entry['message']}".strip())
                    elif entry["event"] == "file":
                        job.files[entry["file"]] = FileStatus(
                            entry["file"], entry["status"], entry.get("message", ""))
                    elif entry["event"] == "flag":
                        job.flags.append(entry["message"])
                    elif entry["event"] == "error":
                        job.errors.append(UserError(
                            entry["error_class"], entry["message"], entry.get("detail", "")))
                    elif entry["event"] == "output":
                        path = Path(entry["file"])
                        if path.exists():
                            job.outputs.append(path)
                    elif entry["event"] == "finished":
                        job.status = entry.get("status", "fertig")
                        job.finished = datetime.fromisoformat(entry["ts"])
            except (json.JSONDecodeError, KeyError, ValueError):
                job.status = "fehler"
            if job.status == "läuft":      # server died mid-run
                job.status = "fehler"
                job.errors.append(UserError(
                    "server-restart", "Lauf wurde durch einen Server-Neustart unterbrochen.", ""))
            self.jobs[job.id] = job
