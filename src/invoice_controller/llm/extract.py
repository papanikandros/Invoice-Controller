from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelHTTPError

from invoice_controller.models import DocumentKind, OfferHeader, OfferTotals, Position

_OutT = TypeVar("_OutT")

load_dotenv()

# pydantic_ai's Google provider reads GOOGLE_API_KEY; the user's .env convention is GEMINI_API_KEY.
if os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


class ExtractedOffer(BaseModel):
    """The structured output we ask the LLM to produce per cost-document PDF."""

    doc_type: DocumentKind = DocumentKind.OFFER
    header: OfferHeader
    positions: list[Position]
    totals: OfferTotals


SYSTEM_PROMPT = """You extract structured data from German cost documents for an EEW Modul 4 energy-efficiency consulting workflow. The German consultant uses your output to prepare a Verwendungsnachweis (BAFA proof-of-funds-usage). Correctness is non-negotiable: the consultant always reviews, so honest "field missing" beats fabricated values.

Most documents are vendor OFFERS (Angebote): a structured position table with a stated Nettosumme/Gesamtpreis. A minority are client STATEMENTS / cost estimates (a 'Stellungnahme', 'Eigenerklärung', or letter listing 'Kosten … für die noch kein Angebot vorliegt') — see (R11). First decide which kind you are reading and set doc_type accordingly ("offer" or "statement"). The rules below are written for offers; (R11) overrides them where it conflicts for statements.

CRITICAL RULES, in order of importance:

(R1) A PRICED POSITION is a line item that has its OWN explicit price (Gesamtpreis / G-Preis / line total in EUR) attributable to that specific position. Narrative bullet points that describe sub-components WITHIN a parent position's scope ("60 Meter Kabeltrasse...", "2 Stück Sicherungslasttrennleisten...") and DO NOT have their own G-Preis are NOT separate positions — they belong in the parent position's description. Positions are numbered consecutively (1, 2, 3, ...); descriptive sub-items have no position number on their line.

(R1b) BUNDLING RULE for positions without individual prices: if the document gives ONLY a group price for several consecutive positions (e.g., "Gesamtpreis Pos. 1 – 9 = 211.500,00 €" with NO individual line totals for Pos 1, 2, …, 9), record those as a SINGLE bundled entry with pos like "1-9" and a description that summarises what's in the bundle. Continue extracting any LATER positions that DO have individual prices (Pos 10, 11, …) as their own entries. DO NOT bundle positions that have individual prices, and DO NOT bundle the ENTIRE offer into one entry when individual prices exist for some positions — only bundle what the document itself bundled.

(R2) GROUP SUBTOTALS ARE NOT POSITIONS. Lines like "Gesamtpreis Pos. 1 – 9", "Σ 1...11", "Zwischensumme", "Gesamtsumme" are ROLLED-UP SUMS of multiple positions. They must NEVER be returned as a single position, and their value must NEVER be attributed to any one position. If you see "Gesamtpreis Pos. 1 – 9 = 211.500,00 €", that means positions 1 through 9 individually sum to 211.500 — find the nine individual position values, do not put 211.500 on Pos 1.

(R3) If the document shows BOTH a "Gesamtpreis Pos.1 – Pos.N" AND a "Sonderpreis Pos.1 – Pos.N", record the Gesamtpreis as totals.nettosumme (Σ of all positions) and the Sonderpreis SEPARATELY in totals.sonderpreis. Do NOT subtract them, do NOT invent a Nachlass position. The consultant decides downstream how to handle the Sonderpreis differential.

(R3b) A DOCUMENT-LEVEL DISCOUNT is a single line near the bottom of the position table or in the footer area that reduces the entire offer total, e.g. "Preisnachlass netto EUR: - 20.260,00", "Skonto auf Gesamtsumme", "Globaler Rabatt". This is NOT a position. Record its amount in totals.preisnachlass (as a positive number, no minus sign). The Nettosumme remains the gross sum of all positions BEFORE this discount. Only individual position lines (with their own Pos. number in the position table) count as positions, even if their price is negative.

(R4) The SUM of line_total_net across all MANDATORY (non-optional) positions you return MUST equal totals.nettosumme. If you cannot make that sum match the document's stated Nettosumme (or Gesamtpreis Pos.1 – Pos.N when no separate Nettosumme is given), you have either missed a position, double-counted one, or counted a group subtotal as a position. Re-check before submitting. OPTIONAL positions (see R10) are excluded from this constraint — they may sit inside or outside the stated total. When the document states BOTH an "ohne Optionen" / "aller Komponenten" total and an "inkl. Optionen" total, use the WITHOUT-options figure as totals.nettosumme and mark the option lines optional.

(R5) Numbers are in German format: "1.234,56" means one thousand two hundred thirty-four point fifty-six. Return decimal STRINGS with dot as decimal separator: "1234.56". Never leave thousand-separator dots in the value.

(R6) Dates: prefer ISO format "YYYY-MM-DD". German "DD.MM.YYYY" is also acceptable. Look for keywords like "Datum", "Belegdatum", "Angebotsdatum", or the date in the offer header.

(R7) Position descriptions are a SHORT NAME, not the full scope text. Include ONLY the product/service name and its type/model/designation (e.g. article name, model code, "Montage", "Inbetriebnahme"). Do NOT include the purpose, application context, scope of work, technical specs, or the long descriptive prose — those belong nowhere in this field. Aim for a concise label and keep it under 80 characters; if the document's name is longer, abbreviate to the essential name + type. Examples: "Radial-Kühltürme KT-1.000-25-FU", "Schalt- und Regelschrank Rückkühlanlage", "Richtpreis Montage". Multi-line names get joined with single spaces. Do NOT include the position number itself in the description string. Trim trailing whitespace and punctuation.

(R8) Classify each position into one of three BAFA categories:
   - investitionskosten: capital goods (Anlagen, Geräte, Maschinen, Materialien, Schaltschränke, Wärmetauscher, Pumpen, Hardware, IT-Komponenten, Mess- und Steuerungstechnik, Schnittstellen)
   - nebenkosten: ancillary services (Montage, Installation, Inbetriebnahme, Schulung, Programmierung (when bundled with installation), Reisekosten, Planungs- und Projektierungsleistungen, Wartungspauschalen)
   - nachlass: explicit discount / rebate (a position whose price is negative or whose description is "Nachlass", "Rabatt", "Preisnachlass", "Skonto").
   For mixed positions (capital goods + service combined into one line), choose by majority intent — favour nebenkosten only when the line is primarily a service deliverable; pure goods purchases are investitionskosten even if a small Lohnanteil is bundled in.
   Include a brief German rationale in kategorie_reason.

(R9) When a field is genuinely absent from the document, OMIT it. Do not fabricate numbers, do not guess. Customer_address, mwst_pct, sonderpreis, artikelnummer, etc. are all optional and should be null when missing.

(R10) OPTIONAL POSITIONS. Capture EVERY priced cost line, including ones the customer can choose to leave out — never silently drop them. A position is OPTIONAL (set optional=true and optional_reason to the German marker) when the document marks it as not part of the binding base scope, by ANY of these signals:
   - an explicit label: "Optionalposition", "optionale Position", "Option", "Eventualposition", "Bedarfsposition", "Alternativposition", "Wahlposition";
   - its price shown in PARENTHESES, e.g. "(Gesamtpreis: 23.700,00 €)", while neighbouring mandatory lines are unparenthesised;
   - it is listed under a separate options section, or after a total that is qualified as "ohne Optionen" / "aller Komponenten" / "Gesamtnettowert (ohne Optionen)";
   - it is a SURCHARGE line — "Mehrpreis", "Aufpreis", "Aufschlag", "Zuschlag" — EVEN WHEN that line is included in the document's stated Gesamtpreis. Flag it optional so the consultant can decide to drop it; it still keeps its own pos number and price.
   Still capture the PRICE: use the stated line total (Betrag / Gesamtpreis); if only a unit price and quantity are given, compute qty × unit price; if ONLY a per-unit rate is given with no quantity (e.g. an Eventualposition "PVC-Verrohrung 95,00 €/m"), leave line_total_net null and put the rate text into the description so nothing is lost. A bare narrative mention of options with NO price or rate is NOT a position (R1 still applies) — do not invent one.

(R11) CLIENT STATEMENTS / COST ESTIMATES (doc_type = "statement"). Some documents are NOT vendor offers but a client's own statement listing assumed costs for planned measures for which no offer exists yet — e.g. a 'Stellungnahme' / letter introducing the costs with wording like "Kosten … für die noch kein Angebot vorliegt" / "geschätzte Kosten" / "angenommene Kosten". Signs: written by the funded company itself (the Zuwendungsempfänger), no offer number, no position table, no Nettosumme/Gesamtpreis, costs given as a simple prose bullet list "<Bezeichnung>: <Betrag> Euro". For these:
   - Set doc_type = "statement".
   - Extract EACH cost line as a position: pos = a sequential index ("1", "2", …) since the document gives none, description = the measure name, line_total_net = the Euro amount, kategorie classified per (R8). If a line is explicitly tagged "(Nebenkosten)" (or Investitionskosten), honour that tag.
   - EXCLUDE non-cost operating metrics: any figure that is NOT a Euro cost — e.g. consumption/quantity values in t/a, kg, kWh, kWh/a, %, Stück, m², m³, or other operating key figures (Betriebskennzahlen). These are NOT positions; never give them a line_total_net. Only lines with a Euro AMOUNT are positions.
   - There is NO Nettosumme: leave totals.nettosumme (and the other totals) null. The cross-sum rule (R4) does NOT apply to statements — do not invent a total, do not force the lines to sum to anything.
   - header.vendor_name = the issuing company (the client itself); header.offer_number = the document's reference if it has one, else a short label like "Stellungnahme"; header.offer_date = the document date; header.title = a short subject if the letter has one.
   - Amounts are treated as netto (the funding basis) even though the document only says "Euro".
"""


@lru_cache(maxsize=1)
def get_agent() -> Agent[None, ExtractedOffer]:
    return Agent(
        _resolve_model(),
        output_type=ExtractedOffer,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
    )


def _resolve_model() -> str:
    """Pick a provider+model by which API key is present. Preference order is OpenAI → Gemini → Anthropic.

    Override the per-provider model via env vars: OPENAI_MODEL, GEMINI_MODEL, ANTHROPIC_MODEL.
    Override the explicit provider via LLM_MODEL (e.g. "openai:gpt-5-mini")."""
    explicit = os.environ.get("LLM_MODEL")
    if explicit:
        return explicit
    if os.environ.get("OPENAI_API_KEY"):
        return f"openai-chat:{os.environ.get('OPENAI_MODEL', 'gpt-5-mini')}"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return f"google:{os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')}"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return f"anthropic:{os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001')}"
    raise RuntimeError(
        "No LLM provider configured. Add OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY to .env."
    )


def _run_with_http_retry(agent: Agent[None, _OutT], user: str | list[Any]) -> _OutT:
    """Retry transient API errors (503 capacity, 429 rate-limit) with backoff. pydantic_ai's
    built-in retries cover validation failures only, not upstream HTTP errors. `user` is either
    a plain prompt string or a multimodal message list (text + image BinaryContent)."""
    backoffs = [4.0, 8.0, 20.0, 60.0]
    last_exc: BaseException | None = None
    for attempt in range(len(backoffs) + 1):
        try:
            return agent.run_sync(user).output
        except ModelHTTPError as exc:
            transient = exc.status_code in (429, 503) or 500 <= exc.status_code < 600
            if not transient or attempt == len(backoffs):
                raise
            wait = 60.0 if exc.status_code == 429 else backoffs[attempt]
            last_exc = exc
            time.sleep(wait)
    if last_exc:
        raise last_exc
    raise RuntimeError("retry loop exited without result")


def extract_offer_llm(
    pages: list[str],
    source_path: Path,
    agent: Agent[None, ExtractedOffer] | None = None,
) -> tuple[OfferHeader, list[Position], OfferTotals, DocumentKind]:
    paginated = "\n".join(f"<<PAGE {i}>>\n{p}" for i, p in enumerate(pages, start=1))
    user = (
        "Below is the layout-preserved text of one German cost-document PDF (a vendor offer "
        "or a client statement/estimate). Extract it per the rules in the system prompt.\n\n"
        f"{paginated}"
    )
    runner = agent or get_agent()
    extracted = _run_with_http_retry(runner, user)
    return extracted.header, extracted.positions, extracted.totals, extracted.doc_type


def extract_offer_llm_vision(
    page_pngs: list[bytes],
    source_path: Path,
    agent: Agent[None, ExtractedOffer] | None = None,
) -> tuple[OfferHeader, list[Position], OfferTotals, DocumentKind]:
    """Vision fallback for scanned PDFs with no text layer: send the rendered page images to
    the (multimodal) extraction agent under the same SYSTEM_PROMPT. The model reads the scan
    directly instead of relying on a text layer that does not exist."""
    message: list[Any] = [
        "The following page image(s) are one German cost-document PDF (a vendor offer or a "
        "client statement/estimate) that has NO text layer — read the text directly from the "
        "image(s) and extract it per the rules in the system prompt. Pages are in order.",
    ]
    for png in page_pngs:
        message.append(BinaryContent(data=png, media_type="image/png"))
    runner = agent or get_agent()
    extracted = _run_with_http_retry(runner, message)
    return extracted.header, extracted.positions, extracted.totals, extracted.doc_type
