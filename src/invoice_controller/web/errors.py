"""U1 error contract: every failure class becomes a German user message + a
structured log entry — no raw stack traces, no silent failures.

Check FAILURES (cross-sum, grounding, missing proofs) are NOT errors: they render
as the yellow/red review flags they are. This module maps genuine errors —
unreadable inputs, provider outages, configuration problems, disk/timeout/cancel.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserError:
    error_class: str      # stable machine-readable tag for audit.jsonl
    message_de: str       # what the colleague reads
    detail: str           # technical detail for the log/support


def describe_error(exc: BaseException, *, filename: str | None = None) -> UserError:
    """Best-effort mapping; the fallback stays honest ('unerwarteter Fehler') and
    keeps the exception type for the log."""
    prefix = f"{filename}: " if filename else ""
    detail = f"{type(exc).__name__}: {exc}"
    name = type(exc).__name__
    text = str(exc)

    def make(error_class: str, message: str) -> UserError:
        return UserError(error_class, prefix + message, detail)

    # Provider / LLM layer (pydantic_ai exception names checked by name so this
    # module needs no LLM imports).
    if name == "ModelHTTPError":
        status = getattr(exc, "status_code", None)
        if status == 429:
            return make(
                "llm-rate-limit",
                "LLM-Anbieter meldet Überlastung (Rate-Limit) — auch nach Warte-Wiederholungen. "
                "Bitte in einigen Minuten erneut starten.",
            )
        if status == 401 or status == 403:
            return make("llm-auth", "LLM-Zugang abgelehnt — API-Schlüssel auf dem Server prüfen.")
        return make(
            "llm-unavailable",
            "LLM-Anbieter nicht erreichbar (Serverfehler) — auch nach Warte-Wiederholungen. "
            "Bitte später erneut starten.",
        )
    if name == "UnexpectedModelBehavior":
        return make(
            "llm-bad-response",
            "Das LLM lieferte wiederholt keine verwertbare Antwort für dieses Dokument.",
        )
    if "No LLM provider configured" in text:
        return make("llm-not-configured", "Kein LLM-Anbieter konfiguriert — .env auf dem Server prüfen.")

    # Input documents.
    if name in ("PdfReadError", "PdfStreamError", "EmptyFileError", "WrongPasswordError", "DependencyError"):
        return make(
            "bad-pdf",
            "PDF nicht lesbar (beschädigt oder passwortgeschützt) — Datei prüfen und erneut hochladen.",
        )
    if name == "FileNotFoundError":
        return make("missing-file", "Datei nicht gefunden — Upload wiederholen.")
    if name == "PermissionError":
        return make("permission", "Zugriff auf die Datei verweigert (Serverproblem).")
    if name == "OSError" and ("No space left" in text or "ENOSPC" in text):
        return make("disk-full", "Kein Speicherplatz mehr auf dem Server — Administrator informieren.")

    # Job lifecycle.
    if name == "CancelledError":
        return make("cancelled", "Lauf wurde abgebrochen.")
    if name == "TimeoutError":
        return make("timeout", "Zeitüberschreitung — der Lauf wurde abgebrochen.")

    # Project-level, already-German messages from the pipeline.
    if name in ("BegProjectError", "BadParameter") or (text and any(
        marker in text for marker in ("Keine ", "kein ", "fehlt")
    ) and name in ("ValueError", "RuntimeError", "BegProjectError")):
        return make("project-input", text or "Eingaben unvollständig.")

    return make(
        "unexpected",
        f"Unerwarteter Fehler ({name}) — im Protokoll vermerkt, bitte an Konstantinos melden.",
    )
