"""vne-generation orchestrator: project folder → classified docs → ratios → invoices → VneResult.

Ratio source (see vne/ratios.py): the consultant's verified Kostenaufstellung as PDF
(Phase 2c V1, the only sheet input since 2026-10-04), else live cost-estimation offer
extraction (the only tier that costs offer-side LLM calls). Invoice extraction always
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
    blocks_from_kostenaufstellung_pdf,
    blocks_from_offer_documents,
    build_abgleich,
)
from invoice_controller.vne.compute import VneResult, compute_vne
from invoice_controller.vne.ratios import from_offer_documents, load_vendor_ratios
from invoice_controller.vne.rename import plan_renames


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
    ratio_path = project_dir / ratio_source.split(":", 1)[1] if ":" in ratio_source else None
    # Uploads that are neither classified (PDF/image) nor the Kostenaufstellung PDF in
    # use — e.g. an .xlsx/.ods, which is no input any more — are listed, never dropped.
    classified_paths = {c.path for c in classified}
    ignored += sorted(
        p for p in project_dir.rglob("*")
        if p.is_file() and p not in classified_paths and p != ratio_path
        and not p.name.startswith((".", "~")) and ".~lock" not in p.name
        and p.name != "projekt.yaml"
    )
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
    result.ratio_path = ratio_path

    # V6: every invoice gets its canonical name right after extraction; the Beleg
    # column and the ZIP download use it (the files themselves are copied by the caller).
    result.renames = plan_renames(
        [r.invoice for r in result.invoices],
        unusable={r.invoice.source_path for r in result.invoices if r.unusable},
    )
    by_path = {r.original: r.new_name for r in result.renames}
    for row in result.invoices:
        row.beleg_name = by_path.get(row.invoice.source_path, row.invoice.source_path.name)

    # Positionsabgleich: reuses what this run already has — no second extraction.
    # The offer side follows the ratio source, so both rest on the same document.
    if offers:
        blocks, offer_source = blocks_from_offer_documents(offers), "Live-Extraktion der Angebote"
    elif ratio_source.startswith("pdf:") and ratio_path is not None:
        blocks, offer_source = blocks_from_kostenaufstellung_pdf(ratio_path), ratio_path.name
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
