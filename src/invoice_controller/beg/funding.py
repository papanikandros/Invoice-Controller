"""FundingMeta — the BEG program parameters, extracted from the project's own documents.

Decided 2026-08-27: no beg.yaml, no user input. The Antragsbestätigung/BzA and the
Zuwendungsbescheid (or KfW-Zusage) carry everything the Kostenzusammenstellung's
footer and Förderung formulas need: Vorgangsnummer/BzA-ID, dates, geplante
förderfähige Kosten, Fördersatz + cap — and the Antragsteller, from which the LLM
classifies the client basis (privat → brutto eligible, Unternehmen → netto).
Anything the documents don't yield stays None and renders red-flagged "fehlt",
never guessed.

Same extraction architecture as the offer/invoice layers: one multimodal
pydantic_ai agent at temperature 0, text path first, page images for scans.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
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
from invoice_controller.normalize import normalize_text
from invoice_controller.pdf.ocr import is_text_layer_empty, render_page_pngs
from invoice_controller.pdf.text import extract_pages


class BegProgramType(str, Enum):
    EH = "eh"          # Effizienzhaus (KfW) — BnD template
    EM = "em"          # Einzelmaßnahme (BAFA / KfW 458 Heizungsförderung) — EM template
    UNCLEAR = "unclear"


class ClientBasis(str, Enum):
    """Whether eligible costs count brutto or netto. Private clients cannot deduct
    VAT → brutto; vorsteuerabzugsberechtigte businesses → netto (Buttergasse case)."""

    PRIVAT = "privat"          # → brutto basis
    UNTERNEHMEN = "unternehmen"  # → netto basis
    UNCLEAR = "unclear"


class FundingMeta(BaseModel):
    """Typed output of the funding-document extraction. Every field is nullable —
    an absent document or figure stays None and is red-flagged downstream."""

    model_config = ConfigDict(extra="forbid")

    program_type: BegProgramType = BegProgramType.UNCLEAR
    program_label: str | None = Field(
        default=None, description="e.g. 'Heizungsförderung (KfW 458)', 'BEG EM', 'BEG Effizienzhaus'"
    )
    vorgangsnummer: str | None = Field(
        default=None, description="Vorgangsnummer / BzA-ID / KfW-Zuschussnummer"
    )
    antrag_date: date | None = None
    bescheid_date: date | None = None
    antragsteller_name: str | None = None
    client_basis: ClientBasis = ClientBasis.UNCLEAR
    client_basis_reason: str | None = Field(
        default=None, description="Why the basis was chosen, e.g. 'GmbH & Co. KG im Antragsteller'"
    )
    geplante_kosten_massnahmen: Decimal | None = Field(
        default=None, description="Geplante förderfähige Kosten der Maßnahmen laut Antrag/BzA"
    )
    geplante_kosten_baubegleitung: Decimal | None = None
    foerdersatz_pct: Decimal | None = Field(
        default=None,
        description="APPLIED Fördersatz der Maßnahmen in Prozent incl. Boni (z.B. 15 Basis + 5 iSFP-Bonus = 20)",
    )
    foerdersatz_zusammensetzung: str | None = Field(
        default=None,
        description="Split when a bonus applies, e.g. '15 % + 5 % iSFP-Bonus'; null when no bonus",
    )
    foerderfaehige_kosten_cap: Decimal | None = Field(
        default=None, description="Höchstgrenze der förderfähigen Kosten, z.B. 30000 / 60000 / 120000"
    )
    baubegleitung_foerdersatz_pct: Decimal | None = Field(
        default=None, description="Fördersatz Baubegleitung/Fachplanung, üblich 50"
    )
    baubegleitung_kosten_cap: Decimal | None = Field(
        default=None, description="Höchstgrenze Baubegleitung, üblich 5000 (EFH)"
    )
    eh_standard: str | None = Field(
        default=None, description="Effizienzhaus-Standard laut BzA, z.B. 'EH-55-WPB', 'EH 40 EE' (EH projects only)"
    )
    wohneinheiten: int | None = Field(
        default=None, description="Anzahl Wohneinheiten laut Antrag/BzA"
    )

    def missing_fields(self) -> list[str]:
        out = []
        if self.program_type is BegProgramType.UNCLEAR:
            out.append("Programmtyp (EH/EM)")
        if self.vorgangsnummer is None:
            out.append("Vorgangsnummer")
        if self.antrag_date is None:
            out.append("Antragsdatum")
        if self.client_basis is ClientBasis.UNCLEAR:
            out.append("Brutto/Netto-Basis (privat/Unternehmen)")
        if self.geplante_kosten_massnahmen is None:
            out.append("geplante förderfähige Kosten")
        if self.foerdersatz_pct is None:
            out.append("Fördersatz")
        return out


SYSTEM_PROMPT = """You extract the funding parameters of ONE German BEG project (Bundesförderung für effiziente Gebäude) from its application and approval documents: Antragsbestätigung / Bestätigung zum Antrag (BzA) / Förderantrag, and Zuwendungsbescheid / KfW-Zusageschreiben / Festsetzungsbescheid. Correctness is non-negotiable: an honest null beats any fabricated value — everything you omit is red-flagged for the consultant, everything you state is trusted.

RULES:

(P1) PROGRAM TYPE: "eh" for a KfW Effizienzhaus project (Bestätigung zum Antrag / BzA, Effizienzhaus-Standard like EH-55); "em" for an Einzelmaßnahme (BAFA BEG EM application, or the KfW 458 Heizungsförderung with an Antragsbestätigung/Zusage). Use "unclear" only when the documents genuinely do not say. Put a short human label into program_label.

(P2) IDENTIFIERS AND DATES: vorgangsnummer is the Vorgangsnummer, BzA-ID, or KfW-Zuschussnummer exactly as printed (keep dashes). antrag_date = date of application/BzA creation; bescheid_date = date of the Zuwendungsbescheid/Zusage. ISO format YYYY-MM-DD.

(P3) FIGURES: geplante_kosten_massnahmen = the geplante förderfähige Kosten of the technical measures per the application; geplante_kosten_baubegleitung = the same for Baubegleitung/Fachplanung when stated separately. foerdersatz_pct = the TOTAL applied funding rate in percent INCLUDING any bonus: when the documents mention an iSFP-Bonus (individueller Sanierungsfahrplan, +5 percentage points) or another Bonus on top of the base rate, add it (e.g. 15 base + 5 iSFP = 20) and record the split in foerdersatz_zusammensetzung ('15 % + 5 % iSFP-Bonus'); without a bonus, foerdersatz_zusammensetzung is null; foerderfaehige_kosten_cap = the maximum eligible cost the rate applies to (e.g. 30000 for Heizung, 60000 for an EM Gebäudehülle measure, 120000 WEG). baubegleitung_foerdersatz_pct / baubegleitung_kosten_cap analogously (typically 50 % up to 5000). DO NOT confuse the project's own geplante/beantragte Baubegleitung costs (→ geplante_kosten_baubegleitung) with the PROGRAM's Höchstgrenze (→ baubegleitung_kosten_cap): the cap is the program-level maximum ('höchstens', 'bis zu', 'maximal förderfähig'); when only the project's planned figure is stated, leave baubegleitung_kosten_cap null rather than repeating it. German numbers: "30.000,00" = 30000.00; return decimal strings with dot separator. For Effizienzhaus projects also record eh_standard (the EH standard incl. suffixes, e.g. 'EH-55-WPB') and wohneinheiten (number of dwelling units) when stated.

(P4) CLIENT BASIS: antragsteller_name = the applicant exactly as printed. client_basis = "unternehmen" when the Antragsteller is a business (legal form GmbH, AG, GmbH & Co. KG, e.K., OHG, UG, or the documents state Vorsteuerabzug) — eligible costs then count netto; "privat" when the Antragsteller is a natural person / private household — costs count brutto; "unclear" otherwise. Give the deciding signal in client_basis_reason.

(P5) Multiple documents may repeat a figure; the Zuwendungsbescheid/Zusage wins over the application on any conflict. Never sum figures across documents. When a field is genuinely absent, return null — do not guess."""


@lru_cache(maxsize=1)
def get_funding_agent() -> Agent[None, FundingMeta]:
    return Agent(
        _resolve_model(),
        output_type=FundingMeta,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
        model_settings=DETERMINISTIC_SETTINGS,
    )


def _doc_content(path: Path) -> list[Any]:
    """One document's contribution to the extraction message: labelled text when a
    text layer exists, rendered page images otherwise (same tiering as the extractors;
    local OCR is skipped here — funding documents are usually born-digital, and the
    vision model reads the rare scanned Bescheid directly)."""
    try:
        pages = [normalize_text(p) for p in extract_pages(path)]
    except Exception:
        pages = []
    if not is_text_layer_empty(pages):
        joined = "\n".join(pages)
        return [f"<<DOKUMENT {path.name}>>\n{joined}"]
    try:
        pngs = render_page_pngs(path)
    except Exception:
        # A corrupt/unreadable file must not kill the funding extraction — the agent
        # is told it exists but is unreadable, and the affected fields stay None.
        return [f"<<DOKUMENT {path.name} — nicht lesbar (weder Textebene noch renderbar)>>"]
    parts: list[Any] = [f"<<DOKUMENT {path.name} — Scan, siehe Seitenbilder>>"]
    parts.extend(BinaryContent(data=png, media_type="image/png") for png in pngs)
    return parts


def extract_funding_meta(
    paths: list[Path],
    agent: Agent[None, FundingMeta] | None = None,
) -> FundingMeta:
    """Extract the FundingMeta from the project's classified funding documents
    (antragsbestaetigung + zuwendungsbescheid). Empty input → all-None meta, so the
    caller renders every footer cell red-flagged instead of failing."""
    if not paths:
        return FundingMeta()
    message: list[Any] = [
        "Below are the funding documents of one BEG project. Extract the funding "
        "parameters per the rules in the system prompt.",
    ]
    for path in paths:
        message.extend(_doc_content(path))
    runner = agent or get_funding_agent()
    return _run_with_http_retry(runner, message)
