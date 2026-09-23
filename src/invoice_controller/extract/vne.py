"""vne-generation orchestrator: project folder → classified docs → ratios → invoices → VneResult.

Ratio source priority (see vne/ratios.py): the verified cost-estimation `Kostenaufstellung.ods`
first, a consultant-built Kostenaufstellung PDF second, live cost-estimation offer extraction
last (the only tier that costs offer-side LLM calls). Invoice extraction always
runs per invoice-classified PDF; `other` documents are carried through by name so
the renderer and CLI can report them — classified and visible, never silently
dropped.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic_ai import Agent

from invoice_controller.config import ProjektConfig
from invoice_controller.extract.classify import DocClass, classify_folder
from invoice_controller.extract.invoice import extract_invoice
from invoice_controller.extract.offer import extract_offer
from invoice_controller.llm.extract_invoice import ExtractedInvoice
from invoice_controller.models import InvoiceDocument
from invoice_controller.vne.abgleich import (
    SKIPPED_NO_POSITIONS,
    AbgleichResult,
    blocks_from_kostenaufstellung_xlsx,
    blocks_from_offer_documents,
    build_abgleich,
)
from invoice_controller.vne.compute import VneResult, compute_vne
from invoice_controller.vne.ratios import from_offer_documents, load_vendor_ratios


def build_vne_tabelle(
    project_dir: str | Path,
    config: ProjektConfig,
    agent: Agent[None, ExtractedInvoice] | None = None,
    on_progress=None,
    with_llm_match: bool = True,
) -> VneResult:
    project_dir = Path(project_dir)
    # A1: a configured client is masked out of every text-path LLM payload.
    mask = (config.client.name, config.client.address) if config.client else None
    classified = classify_folder(project_dir)
    invoices_cls = [c for c in classified if c.doc_class is DocClass.INVOICE]
    offers_cls = [c for c in classified if c.doc_class is DocClass.OFFER]
    # Everything that is neither invoice nor offer (payment proofs, Bescheide,
    # Antragsbestätigungen, other paperwork) is reported by name, never dropped.
    ignored = [
        c.path for c in classified if c.doc_class not in (DocClass.INVOICE, DocClass.OFFER)
    ]

    ratios, ratio_source = load_vendor_ratios(project_dir)
    offers: list = []
    if not ratios and offers_cls:
        # No existing Kostenaufstellung — run cost-estimation extraction on the offers (LLM calls).
        for c in offers_cls:
            if on_progress:
                on_progress(f"Live-Extraktion (Angebot): {c.path.name}")
            offers.append(extract_offer(c.path, with_narrative=False, mask=mask))
        ratios = from_offer_documents(offers)
        ratio_source = "live-extraction"

    invoices: list[InvoiceDocument] = []
    for c in invoices_cls:
        if on_progress:
            on_progress(f"Rechnungs-Extraktion: {c.path.name}")
        try:
            invoices.append(extract_invoice(c.path, agent=agent, mask=mask))
        except Exception as exc:  # noqa: BLE001 — one broken PDF must not kill the run
            print(f"  ! {c.path.name}: Extraktion fehlgeschlagen ({exc})", file=sys.stderr)
            ignored.append(c.path)

    invoices.sort(key=lambda i: (i.invoice_date, i.source_path.name))
    result = compute_vne(
        invoices, ratios, config, ignored=ignored, ratio_source=ratio_source
    )

    # Positionsabgleich: reuses what this run already has — no second extraction.
    # The offer side follows the ratio source, so both rest on the same document.
    if offers:
        blocks, offer_source = blocks_from_offer_documents(offers), "Live-Extraktion der Angebote"
    elif ratio_source.startswith("xlsx:"):
        xlsx = project_dir / ratio_source.removeprefix("xlsx:")
        blocks, offer_source = blocks_from_kostenaufstellung_xlsx(xlsx), xlsx.name
    else:
        blocks, offer_source = [], ""
    if not blocks:
        result.abgleich = AbgleichResult(skipped_reason=SKIPPED_NO_POSITIONS)
        return result
    # Unusable rows are out of every sum; the consultant's own fee has no vendor
    # offer by design (fixed EK line) — neither belongs in a vendor scope check.
    matchable = [r.invoice for r in result.invoices if not r.unusable and not r.own_company]
    result.abgleich = build_abgleich(
        blocks, matchable, offer_source=offer_source, with_llm=with_llm_match,
        on_progress=on_progress or (lambda _m: None),
    )
    return result
