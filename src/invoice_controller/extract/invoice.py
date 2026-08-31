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
from invoice_controller.extract.einvoice import find_embedded_invoice_xml, parse_cii_invoice
from invoice_controller.extract.grounding import check_amounts_grounded
from invoice_controller.llm.extract import RETRY_SETTINGS
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


def _position_check(extracted: ExtractedInvoice):
    return check_invoice_positions(
        extracted.positions,
        extracted.netto,
        cumulative_netto=extracted.cumulative_netto,
        brutto=extracted.brutto,
    )


def _grounding_amounts(extracted: ExtractedInvoice) -> dict[str, Decimal | None]:
    """The STATED fields the R2 guard checks — never derived values."""
    amounts: dict[str, Decimal | None] = {
        "Netto": extracted.netto,
        "MwSt": extracted.mwst_amount,
        "Brutto": extracted.brutto,
        "Gesamt-Netto": extracted.cumulative_netto,
        "Abschläge": extracted.deducted_advances_netto,
    }
    for i, p in enumerate(extracted.positions, start=1):
        amounts[f"Pos. {p.pos or i}"] = p.line_total_net
    return amounts


def extract_invoice(
    path: Path,
    agent: Agent[None, ExtractedInvoice] | None = None,
    *,
    with_retry: bool = True,
) -> InvoiceDocument:
    # Tier 0 (R1, 2026-08-31): an embedded ZUGFeRD/Factur-X XML is the vendor's own
    # machine-readable invoice — exact amounts and line items, no OCR/LLM risk. The
    # cross-sum still runs, now validating the vendor's XML. A broken XML falls
    # through to the normal chain and is called out in the amount-check message.
    xml_note: str | None = None
    xml = find_embedded_invoice_xml(path)
    if xml is not None:
        try:
            extracted = parse_cii_invoice(xml)
            return InvoiceDocument(
                source_path=path,
                amount_check=check_invoice_amounts(extracted),
                position_check=_position_check(extracted),
                extraction_method="zugferd-xml",
                **extracted.model_dump(),
            )
        except Exception as exc:  # noqa: BLE001 — vendor XML must never break the pipeline
            xml_note = (
                f"eingebettetes E-Rechnungs-XML nicht verwertbar "
                f"({type(exc).__name__}) — LLM-Extraktion verwendet"
            )

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

    # Σ(line items) vs the stated total — the position-level guardrail (2026-08-28).
    # Loud, never blocking: a failure red-flags the row like the amount check.
    position_check = _position_check(extracted)

    # R5 (2026-08-31): ONE bounded resample when the deterministic run fails its
    # cross-sum (Hemmer et al., IJDAR 2025: the correct reading often sits just below
    # top-1). Adopted ONLY if the retry reconciles; recorded via "+retry".
    if with_retry and not position_check.passed:
        if use_vision:
            retried = extract_invoice_llm_vision(
                images, source_path=path, agent=agent, model_settings=RETRY_SETTINGS
            )
        else:
            retried = extract_invoice_llm(
                pages, source_path=path, agent=agent, model_settings=RETRY_SETTINGS
            )
        retried_check = _position_check(retried)
        if retried_check.passed:
            extracted, position_check = retried, retried_check
            method += "+retry"

    # R2: stated amounts must appear verbatim in the text the LLM read; the vision
    # path has no local text to ground against, so the guard is skipped there.
    grounding_check = None
    if not use_vision:
        grounding_check = check_amounts_grounded(
            _grounding_amounts(extracted), "\n".join(pages)
        )

    amount_check = check_invoice_amounts(extracted)
    if xml_note:
        amount_check.message = "; ".join(m for m in (xml_note, amount_check.message) if m)

    return InvoiceDocument(
        source_path=path,
        amount_check=amount_check,
        position_check=position_check,
        grounding_check=grounding_check,
        extraction_method=method,
        **extracted.model_dump(),
    )
