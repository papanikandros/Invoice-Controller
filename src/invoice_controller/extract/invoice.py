"""Invoice extraction orchestrator (vne-generation): pdf → (text | OCR | vision) → LLM → checks.

Identical tiering to extract/offer.py — the OCR path is keyed on "no text layer",
and 35 % of the close-out corpus invoices are scans, so the fallback chain is not
optional here. The amount check is the invoice-side analogue of the offer
cross-sum: it never blocks, it flags.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_ai import Agent

from invoice_controller.extract.cross_sum import check_invoice_positions
from invoice_controller.llm.extract_invoice import (
    ExtractedInvoice,
    extract_invoice_llm,
    extract_invoice_llm_vision,
)
from invoice_controller.models import AmountCheck, InvoiceDocument, InvoiceType, VatStatus
from invoice_controller.normalize import normalize_text
from invoice_controller.pdf.ocr import (
    is_text_layer_empty,
    local_ocr_text,
    render_page_pngs,
)
from invoice_controller.pdf.text import extract_pages

_TOLERANCE = Decimal("0.02")


def check_invoice_amounts(inv: ExtractedInvoice) -> AmountCheck:
    """Internal redundancy over the stated amounts. What can be cross-checked depends
    on the VAT status; anything that cannot be verified is reported, not assumed."""
    problems: list[str] = []

    if inv.vat_status in (VatStatus.TAX_FREE, VatStatus.REVERSE_CHARGE):
        if inv.brutto is not None and abs(inv.brutto - inv.netto) > _TOLERANCE:
            problems.append(
                f"steuerfrei/§13b, aber Brutto {inv.brutto} ≠ Netto {inv.netto}"
            )
    else:
        if inv.brutto is not None and inv.mwst_amount is not None:
            if abs((inv.netto + inv.mwst_amount) - inv.brutto) > _TOLERANCE:
                problems.append(
                    f"Netto {inv.netto} + MwSt {inv.mwst_amount} ≠ Brutto {inv.brutto}"
                )
        if inv.mwst_pct is not None and inv.mwst_amount is not None:
            expected = (inv.netto * inv.mwst_pct / Decimal(100)).quantize(Decimal("0.01"))
            if abs(expected - inv.mwst_amount) > Decimal("0.05"):
                problems.append(
                    f"MwSt {inv.mwst_amount} ≠ Netto × {inv.mwst_pct} % = {expected}"
                )
        if inv.brutto is None and inv.mwst_amount is None and inv.vat_status is not VatStatus.UNCLEAR:
            problems.append("weder Brutto noch MwSt-Betrag zum Gegenprüfen vorhanden")

    if inv.invoice_type is InvoiceType.GUTSCHRIFT and inv.netto > 0:
        problems.append(f"Gutschrift mit positivem Netto {inv.netto}")
    if inv.invoice_type is not InvoiceType.GUTSCHRIFT and inv.netto < 0:
        problems.append(f"negatives Netto {inv.netto} ohne Gutschrift-Kennzeichnung")

    if inv.cumulative_netto is not None and inv.deducted_advances_netto is not None:
        expected_rest = inv.cumulative_netto - inv.deducted_advances_netto
        if abs(expected_rest - inv.netto) > _TOLERANCE:
            problems.append(
                f"Schlussrechnung: Gesamt {inv.cumulative_netto} − Abschläge "
                f"{inv.deducted_advances_netto} ≠ Restbetrag {inv.netto}"
            )

    return AmountCheck(passed=not problems, message="; ".join(problems) or None)


def extract_invoice(
    path: Path,
    agent: Agent[None, ExtractedInvoice] | None = None,
) -> InvoiceDocument:
    pages = [normalize_text(p) for p in extract_pages(path)]
    method = "pdfplumber+llm"
    use_vision = False

    if is_text_layer_empty(pages):
        ocr_pages = local_ocr_text(path)
        if ocr_pages is not None and not is_text_layer_empty(ocr_pages):
            pages = [normalize_text(p) for p in ocr_pages]
            method = "tesseract+llm"
        else:
            use_vision = True

    if use_vision:
        images = render_page_pngs(path)
        extracted = extract_invoice_llm_vision(images, source_path=path, agent=agent)
        method = "vision-llm"
    else:
        extracted = extract_invoice_llm(pages, source_path=path, agent=agent)

    return InvoiceDocument(
        source_path=path,
        amount_check=check_invoice_amounts(extracted),
        # Σ(line items) vs the stated total — the position-level guardrail (2026-08-28).
        # Loud, never blocking: a failure red-flags the row like the amount check.
        position_check=check_invoice_positions(
            extracted.positions,
            extracted.netto,
            cumulative_netto=extracted.cumulative_netto,
            brutto=extracted.brutto,
        ),
        extraction_method=method,
        **extracted.model_dump(),
    )
