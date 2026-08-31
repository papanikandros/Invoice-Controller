"""LLM extraction layer for German invoices (vne-generation).

Same architecture as llm/extract.py for offers: one multimodal agent, typed output,
temperature 0, tiered input (text or page images). The rule set (I1…I10) mirrors the
offer rules' philosophy — the LLM reads, deterministic code verifies — with the
invoice-specific twists established by the close-out corpus: stated netto is the
authoritative figure, Schlussrechnungen book their OWN remaining amount, VAT status
must be classified (steuerfrei vendors exist), Gutschriften are negative.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent, BinaryContent

from invoice_controller.llm.extract import (
    DETERMINISTIC_SETTINGS,
    _resolve_model,
    _run_with_http_retry,
)
from invoice_controller.models import InvoicePosition, InvoiceType, VatStatus


class ExtractedInvoice(BaseModel):
    """The structured output the LLM produces per invoice PDF."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str
    vendor_address: str | None = None
    recipient_name: str | None = None
    recipient_address: str | None = None
    invoice_number: str
    invoice_date: date
    order_ref: str | None = None
    order_date: date | None = None
    invoice_type: InvoiceType = InvoiceType.RECHNUNG
    subject: str | None = None
    netto: Decimal
    mwst_pct: Decimal | None = None
    mwst_amount: Decimal | None = None
    brutto: Decimal | None = None
    vat_status: VatStatus = VatStatus.STANDARD
    skonto_pct: Decimal | None = None
    skonto_deadline: date | None = None
    cumulative_netto: Decimal | None = None
    deducted_advances_netto: Decimal | None = None
    positions: list[InvoicePosition] = []


SYSTEM_PROMPT = """You extract structured data from German invoices (Rechnungen) for a funded-project close-out workflow (EEW Modul 4 / BEG). The consultant uses your output to build the Verwendungsnachweis submission. Correctness is non-negotiable: the consultant always reviews, so an honest "field missing" beats any fabricated value.

CRITICAL RULES, in order of importance:

(I1) NETTO IS THE AUTHORITATIVE FIGURE: `netto` is the net amount payable for THIS invoice document. Read the STATED Nettobetrag/Nettosumme — do not derive it from the brutto. For a SCHLUSSRECHNUNG that deducts prior Anzahlungs-/Abschlagsrechnungen, `netto` is the REMAINING net amount of this final invoice (the cumulative project total goes into `cumulative_netto` and the sum of deducted prior advances into `deducted_advances_netto`). For all other types, `netto` is the invoice's net total.

(I2) INVOICE TYPE from the document's own wording: "Anzahlungsrechnung" → anzahlung; "Abschlagsrechnung"/"Teilrechnung" → teilrechnung; "Schlussrechnung" → schlussrechnung; "Gutschrift" → gutschrift; otherwise → rechnung. A numbered sequence marker like "1. Abschlagsrechnung" is teilrechnung.

(I3) GUTSCHRIFT (credit note): all amounts (`netto`, `mwst_amount`, `brutto`) are NEGATIVE, even if the document prints them without a sign.

(I4) VAT STATUS: classify how VAT appears:
   - "standard": 19 % German VAT charged.
   - "reduced": 7 % German VAT charged.
   - "tax_free": no VAT charged with a legal note like "innergemeinschaftliche Lieferung, steuerfrei" — here brutto equals netto.
   - "reverse_charge": §13b UStG, "Steuerschuldnerschaft des Leistungsempfängers" — brutto equals netto.
   - "unclear": VAT treatment cannot be determined. Never guess a VAT amount.
   Record the stated `mwst_pct`, `mwst_amount` and `brutto` exactly as printed (null when absent).

(I5) Numbers are German format: "1.234,56" = 1234.56. Return decimal STRINGS with dot as decimal separator, no thousand separators.

(I6) Dates: prefer ISO "YYYY-MM-DD". Look for Rechnungsdatum/Belegdatum/Datum for `invoice_date`. An order reference like "gemäß Auftragsbestätigung Nr. 25-4017 vom 18.03.2025" or "Ihre Bestellung vom …" fills `order_ref` and `order_date`.

(I7) `vendor_name` is the party ISSUING the invoice (letterhead/footer with IBAN, USt-IdNr.); `recipient_name`/`recipient_address` is the party being billed (the address block). Do not swap them on a Gutschrift.

(I8) `subject` is a SHORT purpose label for the payment — the offer/project/item the invoice belongs to, e.g. "Werksverrohrung", "Kältemaschine", "Elektroinstallation", "Einsparkonzept". Not the full position text; under 60 characters.

(I9) SKONTO: record offered early-payment terms ("2 % Skonto bei Zahlung bis …") in `skonto_pct`/`skonto_deadline`. "14 Tage netto ohne Abzug" means no Skonto → both null. Never compute a discounted amount yourself.

(I10) When a field is genuinely absent, return it EXPLICITLY as null — output every schema key, never leave one out, never fabricate invoice numbers, dates, or amounts. (Models measurably invent values for absent fields when allowed to skip keys; an explicit null is the honest answer.) If the document turns out not to be an invoice at all (e.g. a contract or payment advice), still fill what exists and set `subject` to a short description of what the document actually is.

(I11) POSITIONS — extract EVERY billed line item into `positions`: pos (the printed position number, "" when unnumbered), description (short name, not the full scope prose), qty, unit, unit_price_net, line_total_net (the line's NET total; NEGATIVE for discount/Nachlass/credit lines). Sub-items without their own price, group subtotals (Zwischensumme, Titelsumme, Übertrag), the Nettosumme/MwSt/Brutto rows, and payment terms are NOT positions. A document-level discount printed as its own line (Rabatt/Nachlass) IS a position with a negative line_total_net.

(I12) POSITION CROSS-SUM INTENT: the extracted positions must sum to the invoice's stated net total — Σ(line_total_net) = Nettosumme (for a Schlussrechnung: the CUMULATIVE net total before deducted advances, since its position table describes the whole project scope). Deterministic code verifies this and flags any mismatch, so never invent, merge, or drop lines to force agreement — extract faithfully what is printed.
"""


@lru_cache(maxsize=1)
def get_invoice_agent() -> Agent[None, ExtractedInvoice]:
    return Agent(
        _resolve_model(),
        output_type=ExtractedInvoice,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
        model_settings=DETERMINISTIC_SETTINGS,
    )


def extract_invoice_llm(
    pages: list[str],
    source_path: Path,
    agent: Agent[None, ExtractedInvoice] | None = None,
    model_settings: dict | None = None,
) -> ExtractedInvoice:
    paginated = "\n".join(f"<<PAGE {i}>>\n{p}" for i, p in enumerate(pages, start=1))
    user = (
        "Below is the layout-preserved text of one German invoice PDF. "
        "Extract it per the rules in the system prompt.\n\n"
        f"{paginated}"
    )
    runner = agent or get_invoice_agent()
    return _run_with_http_retry(runner, user, model_settings=model_settings)


def extract_invoice_llm_vision(
    page_pngs: list[bytes],
    source_path: Path,
    agent: Agent[None, ExtractedInvoice] | None = None,
    model_settings: dict | None = None,
) -> ExtractedInvoice:
    message: list[Any] = [
        "The following page image(s) are one German invoice PDF that has NO text layer — "
        "read the text directly from the image(s) and extract it per the rules in the "
        "system prompt. Pages are in order.",
    ]
    for png in page_pngs:
        message.append(BinaryContent(data=png, media_type="image/png"))
    runner = agent or get_invoice_agent()
    return _run_with_http_retry(runner, message, model_settings=model_settings)
