"""EEW Zuwendungsbescheid extraction (2026-09-03, user request).

The EEW vne-generation config values (Bewilligungszeitraum, Förderbetrag,
Förderanteil, client) live in the BAFA Zuwendungs-/Bewilligungsbescheid. When the
colleague uploads that PDF, its figures pre-fill the run configuration — manually
typed UI fields ALWAYS take precedence, and every adopted value is flagged with
its provenance so the consultant sees what came from where.

Same extraction discipline as beg/funding.py: one pydantic_ai agent at temperature
0, text path first, page images for scans, every field nullable — an absent figure
stays None, never guessed. (This is deliberately NOT the Phase-3 "three-way
comparison" — the Bescheid only feeds the config here, no per-position matching.)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, BinaryContent

from invoice_controller.llm.extract import (
    DETERMINISTIC_SETTINGS,
    _resolve_model,
    _run_with_http_retry,
)
from invoice_controller.extract.evidence import ground_fields
from invoice_controller.normalize import normalize_text
from invoice_controller.pdf.ocr import is_text_layer_empty, render_page_pngs
from invoice_controller.pdf.text import extract_pages


class EewBescheidMeta(BaseModel):
    """Figures from the (latest) EEW Bewilligungs-/Zuwendungsbescheid."""

    model_config = ConfigDict(extra="forbid")

    empfaenger_name: str | None = Field(
        default=None, description="Zuwendungsempfänger (the funded company)"
    )
    empfaenger_adresse: str | None = None
    bewilligungszeitraum_start: date | None = None
    bewilligungszeitraum_end: date | None = None
    foerderbetrag: Decimal | None = Field(
        default=None, description="Bewilligter Förderbetrag / Zuwendung in EUR"
    )
    foerderanteil_pct: Decimal | None = Field(
        default=None, description="Förderquote / Mehrkosten-Anteil in Prozent, e.g. 40"
    )
    # Phase 2c V2 (2026-10-04): the dates the fundability checks need, plus the
    # identifiers the VNE-Maske header carries.
    bescheid_datum: date | None = Field(default=None, description="Datum des Bescheids (letter date)")
    antrag_datum: date | None = Field(
        default=None, description="Datum der Antragstellung as referenced in the letter ('Ihr Antrag vom …')"
    )
    kennung: str | None = Field(
        default=None, description="Vorgangsnummer / Aktenzeichen / Kennung of the application, verbatim"
    )
    # IBAN / Steuernummer are deliberately NOT extracted: the IBAN printed on a
    # Bescheid is BAFA's own Bundesbank account (EK4_333, 2026-10-04), never the client's.
    # E1 (2026-10-06): verbatim quotes for the values the fundability rules depend on.
    bescheid_datum_quote: str | None = None
    antrag_datum_quote: str | None = None
    kennung_quote: str | None = None
    foerderbetrag_quote: str | None = None
    # filled by extract_eew_bescheid after grounding (field → Evidence dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


SYSTEM_PROMPT = """You extract the approval parameters from ONE German EEW Modul 4 Bewilligungsbescheid / Zuwendungsbescheid (BAFA approval letter). An honest null beats any fabricated value — everything you state is trusted downstream.

(B1) empfaenger_name / empfaenger_adresse: the Zuwendungsempfänger (the funded company), as stated.
(B2) bewilligungszeitraum_start / _end: the Bewilligungszeitraum (project window — earliest date costs may count, completion deadline). Dates as YYYY-MM-DD.
(B3) foerderbetrag: the approved Zuwendung/Förderbetrag in EUR. foerderanteil_pct: the Förderquote / Mehrkosten-Anteil in percent (e.g. 40). German numbers: "45.000,00" = 45000.00 — return decimal strings with dot separator.
(B4) If the document is an Änderungsbescheid, extract ITS (amended) figures — the latest Bescheid is binding.
(B7) EVIDENCE QUOTES: for bescheid_datum, antrag_datum, kennung and foerderbetrag also return `<field>_quote` — the shortest VERBATIM passage (≤ 80 characters, copied exactly, with its label) that states the value; null value → null quote; never paraphrase or invent.
(B6) bescheid_datum: the letter's own date ("Eschborn, 26.03.2026"). antrag_datum: the application date the letter refers to ("Ihr Antrag vom 14.04.2025", "Antrag eingegangen am …"). kennung: the Vorgangsnummer / Aktenzeichen / Kennung verbatim (e.g. "EEW-SWG 720002505").
(B5) Return every schema key explicitly; null for anything the document does not state. Never guess."""


@lru_cache(maxsize=1)
def get_bescheid_agent() -> Agent[None, EewBescheidMeta]:
    return Agent(
        _resolve_model(),
        output_type=EewBescheidMeta,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
        model_settings=DETERMINISTIC_SETTINGS,
    )


_META_CACHE: dict[tuple[str, ...], EewBescheidMeta] = {}


def _content_key(paths: list[Path]) -> tuple[str, ...]:
    import hashlib

    return tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)


def extract_eew_bescheid_cached(paths: list[Path]) -> EewBescheidMeta:
    """Upload-time pre-fill (todo #12) and the run both need the meta; keyed by file
    CONTENT so the renamed copy in the run directory hits the same entry."""
    key = _content_key(paths)
    if key not in _META_CACHE:
        _META_CACHE[key] = extract_eew_bescheid(paths)
    return _META_CACHE[key]


def extract_eew_bescheid(
    paths: list[Path], agent: Agent[None, EewBescheidMeta] | None = None
) -> EewBescheidMeta:
    """One call over all Bescheid documents (an Änderungsbescheid amends the first —
    the model is told the latest figures bind)."""
    message: list[Any] = [
        "The following German EEW Bewilligungs-/Zuwendungsbescheid document(s) belong to "
        "ONE project. Extract the approval parameters per the system prompt; if several "
        "documents conflict, the latest (Änderungs-)Bescheid wins.",
    ]
    for path in paths:
        pages = [normalize_text(p) for p in extract_pages(path)]
        if is_text_layer_empty(pages):
            message.append(f"[Dokument (Scan): {path.name}]")
            for png in render_page_pngs(path):
                message.append(BinaryContent(data=png, media_type="image/png"))
        else:
            joined = "\n".join(pages)
            message.append(f"[Dokument: {path.name}]\n{joined}")
    runner = agent or get_bescheid_agent()
    meta = _run_with_http_retry(runner, message)
    text_pages = [p for path in paths for p in extract_pages(path)]
    meta.evidence = {
        f: ev.model_dump() for f, ev in ground_fields(
            meta, ("bescheid_datum", "antrag_datum", "kennung", "foerderbetrag"),
            text_pages if not is_text_layer_empty(text_pages) else None,
        ).items()
    }
    return meta
