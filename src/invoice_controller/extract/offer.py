from __future__ import annotations

import re
import sys
from pathlib import Path

from pydantic_ai import Agent

from invoice_controller.extract.cross_sum import check_offer, check_statement
from invoice_controller.llm.extract import (
    ExtractedOffer,
    extract_offer_llm,
    extract_offer_llm_vision,
)
from invoice_controller.llm.summarize import summarize_offer_costs
from invoice_controller.models import CostNarrative, DocumentKind, OfferDocument
from invoice_controller.normalize import normalize_text
from invoice_controller.pdf.ocr import (
    is_text_layer_empty,
    local_ocr_text,
    render_page_pngs,
)
from invoice_controller.pdf.text import extract_pages

# Filename stems that positively mark a client statement / cost estimate rather than a vendor
# offer. Detection is filename-first (cheap, deterministic); when the name gives no signal we
# fall back to the LLM's doc_type classification. Only STATEMENT is detected positively here —
# any other name returns None and defers to the LLM, so a mis-named statement is still caught.
_STATEMENT_FILENAME_RE = re.compile(
    r"stellungnahme|eigenerkl|eigensch|kostensch|sch[äa]tzung|kostenannahme|kostenvoranschlag",
    re.IGNORECASE,
)


def kind_from_filename(path: Path) -> DocumentKind | None:
    """Classify a document by filename. Returns STATEMENT for statement/estimate names,
    else None (no signal — defer to the LLM's doc_type)."""
    if _STATEMENT_FILENAME_RE.search(path.stem):
        return DocumentKind.STATEMENT
    return None


def extract_offer(
    path: Path,
    agent: Agent[None, ExtractedOffer] | None = None,
    *,
    with_narrative: bool = True,
    summarize_agent: Agent[None, CostNarrative] | None = None,
) -> OfferDocument:
    pages = [normalize_text(p) for p in extract_pages(path)]
    method = "pdfplumber+llm"
    use_vision = False

    # Scanned (image-only) PDFs have no text layer. Recover in tiers: local OCR first
    # (on-prem), then the cloud vision-LLM. See pdf/ocr.py for the rationale.
    if is_text_layer_empty(pages):
        ocr_pages = local_ocr_text(path)
        if ocr_pages is not None and not is_text_layer_empty(ocr_pages):
            pages = [normalize_text(p) for p in ocr_pages]
            method = "tesseract+llm"
        else:
            use_vision = True

    if use_vision:
        images = render_page_pngs(path)
        header, positions, totals, doc_type = extract_offer_llm_vision(
            images, source_path=path, agent=agent
        )
        method = "vision-llm"
    else:
        header, positions, totals, doc_type = extract_offer_llm(
            pages, source_path=path, agent=agent
        )

    # Filename is the primary signal; the LLM's classification is the fallback when the
    # filename is uninformative.
    kind = kind_from_filename(path) or doc_type

    if kind is DocumentKind.STATEMENT:
        cross_sum = check_statement(positions)
    else:
        cross_sum = check_offer(positions, totals)

    narrative: CostNarrative | None = None
    # The narrative reads the page text; the vision path has none, so skip it there.
    if with_narrative and not is_text_layer_empty(pages):
        # The cost narrative is an enrichment for the consultant's Verwendungsnachweis prose;
        # a failure here must never invalidate the (cross-summed) extraction itself.
        try:
            narrative = summarize_offer_costs(pages, agent=summarize_agent)
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort by design
            print(f"  ! cost narrative skipped ({type(exc).__name__}: {exc})", file=sys.stderr)

    return OfferDocument(
        source_path=path,
        header=header,
        positions=positions,
        totals=totals,
        cross_sum=cross_sum,
        kind=kind,
        vat_basis_unstated=(kind is DocumentKind.STATEMENT),
        narrative=narrative,
        extraction_method=method,
    )
