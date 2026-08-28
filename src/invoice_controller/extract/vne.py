"""F2 orchestrator: project folder → classified docs → ratios → invoices → VneResult.

Ratio source priority (see vne/ratios.py): the verified F1 `Kostenaufstellung.ods`
first, a consultant-built Kostenaufstellung PDF second, live F1 offer extraction
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
from invoice_controller.vne.compute import VneResult, compute_vne
from invoice_controller.vne.ratios import from_offer_documents, load_vendor_ratios


def build_vne_tabelle(
    project_dir: str | Path,
    config: ProjektConfig,
    agent: Agent[None, ExtractedInvoice] | None = None,
    on_progress=None,
) -> VneResult:
    project_dir = Path(project_dir)
    classified = classify_folder(project_dir)
    invoices_cls = [c for c in classified if c.doc_class is DocClass.INVOICE]
    offers_cls = [c for c in classified if c.doc_class is DocClass.OFFER]
    # Everything that is neither invoice nor offer (payment proofs, Bescheide,
    # Antragsbestätigungen, other paperwork) is reported by name, never dropped.
    ignored = [
        c.path for c in classified if c.doc_class not in (DocClass.INVOICE, DocClass.OFFER)
    ]

    ratios, ratio_source = load_vendor_ratios(project_dir)
    if not ratios and offers_cls:
        # No existing Kostenaufstellung — run F1 extraction on the offers (LLM calls).
        offers = []
        for c in offers_cls:
            if on_progress:
                on_progress(f"F1-Extraktion (Angebot): {c.path.name}")
            offers.append(extract_offer(c.path, with_narrative=False))
        ratios = from_offer_documents(offers)
        ratio_source = "f1-live"

    invoices: list[InvoiceDocument] = []
    for c in invoices_cls:
        if on_progress:
            on_progress(f"Rechnungs-Extraktion: {c.path.name}")
        try:
            invoices.append(extract_invoice(c.path, agent=agent))
        except Exception as exc:  # noqa: BLE001 — one broken PDF must not kill the run
            print(f"  ! {c.path.name}: Extraktion fehlgeschlagen ({exc})", file=sys.stderr)
            ignored.append(c.path)

    invoices.sort(key=lambda i: (i.invoice_date, i.source_path.name))
    return compute_vne(
        invoices, ratios, config, ignored=ignored, ratio_source=ratio_source
    )
