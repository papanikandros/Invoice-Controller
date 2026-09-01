"""BEG vne-generation orchestrator (B5): project folder → Kostenzusammenstellung .xlsx.

classify (shared 6-class walker) → FundingMeta (EKK documents) → position-level
invoice extraction (the shared extractor: ZUGFeRD tier → text → Tesseract → vision,
cross-sum + retry + grounding) → dedupe (vendor, Re-Nr.), text-layer copy preferred
over the `*_geprüft` scan → Zahlungsnachweis extraction + reconciliation (B4) →
Gewerk/section labels → BegTable → EH/EM writer.

Offers, Fachunternehmererklärungen etc. are classified but ignored in v1 —
reported by name, never processed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from invoice_controller.beg.compute import BegTable, build_table
from invoice_controller.beg.funding import BegProgramType, FundingMeta, extract_funding_meta
from invoice_controller.beg.gewerk import GewerkLabel, label_invoice
from invoice_controller.beg.payments import (
    ProofRecord,
    Reconciliation,
    extract_payment_proofs,
    reconcile_payments,
)
from invoice_controller.beg.xlsx import write_kostenzusammenstellung
from invoice_controller.extract.classify import Classified, DocClass, classify_folder
from invoice_controller.extract.invoice import extract_invoice
from invoice_controller.match.vendor import normalize_vendor
from invoice_controller.models import InvoiceDocument

ProgressFn = Callable[[str], None]


@dataclass
class BegResult:
    meta: FundingMeta
    table: BegTable
    invoices: list[InvoiceDocument]
    duplicates: list[InvoiceDocument]          # dropped (vendor, Re-Nr.) copies
    reconciliations: list[Reconciliation]
    proofs: list[ProofRecord]
    unreadable: list[tuple[Path, str]]         # per-file extraction failures (loud, non-fatal)
    ignored: list[Classified] = field(default_factory=list)
    output_path: Path | None = None


class BegProjectError(Exception):
    """The project folder cannot be processed at all (no funding documents)."""


def _dedupe_key(inv: InvoiceDocument) -> tuple[frozenset[str], str]:
    number = "".join(ch for ch in inv.invoice_number.lower() if ch.isalnum())
    return (frozenset(normalize_vendor(inv.vendor_name)), number)


def _prefer(cur: InvoiceDocument, new: InvoiceDocument) -> tuple[InvoiceDocument, InvoiceDocument]:
    """(winner, loser): a passing position cross-sum beats a failing one, a text-layer
    extraction beats vision; on a full tie the first stays."""
    def rank(inv: InvoiceDocument) -> tuple[int, int]:
        check_ok = inv.position_check is not None and inv.position_check.passed
        return (0 if check_ok else 1, 0 if not inv.extraction_method.startswith("vision") else 1)

    return (cur, new) if rank(cur) <= rank(new) else (new, cur)


def _dedupe(invoices: list[InvoiceDocument]) -> tuple[list[InvoiceDocument], list[InvoiceDocument]]:
    """Two duplicate classes in BEG projects: (vendor, Re-Nr.) — the original AND its
    `*_geprüft` scan — and (Re-Nr., netto) across DIFFERENT vendor readings, seen live
    on Madroch where the vision path read the same stamped invoice with two different
    letterheads. Keep the better copy (see `_prefer`)."""
    kept: dict[tuple[frozenset[str], str], InvoiceDocument] = {}
    dropped: list[InvoiceDocument] = []
    for inv in invoices:
        key = _dedupe_key(inv)
        cur = kept.get(key)
        if cur is None:
            kept[key] = inv
            continue
        winner, loser = _prefer(cur, inv)
        kept[key] = winner
        dropped.append(loser)

    by_number: dict[tuple[str, object], InvoiceDocument] = {}
    result: list[InvoiceDocument] = []
    for inv in kept.values():
        number = "".join(ch for ch in inv.invoice_number.lower() if ch.isalnum())
        num_key = (number, inv.netto)
        cur = by_number.get(num_key)
        if cur is None:
            by_number[num_key] = inv
            continue
        winner, loser = _prefer(cur, inv)
        by_number[num_key] = winner
        dropped.append(loser)
    result = list(by_number.values())
    return result, dropped


def build_kostenzusammenstellung(
    project_dir: Path,
    *,
    output_path: Path | None = None,
    classified: list[Classified] | None = None,
    meta: FundingMeta | None = None,
    program_hint: BegProgramType | None = None,
    on_progress: ProgressFn = lambda _msg: None,
) -> BegResult:
    # `classified`/`meta` can be handed in by a caller that already ran them (the CLI
    # reports both before building) — re-running FundingMeta would double an LLM call.
    if classified is None:
        classified = classify_folder(project_dir)

    if meta is None:
        funding_docs = [
            c.path
            for c in classified
            if c.doc_class in (DocClass.ANTRAGSBESTAETIGUNG, DocClass.ZUWENDUNGSBESCHEID)
        ]
        if not funding_docs:
            # Loud, not blocking (decided 2026-08-31, EH corpus projects ship no EKK
            # docs): the table is still worth producing — every program field renders
            # red "fehlt" and the consultant supplies the Bescheid data by hand.
            on_progress("KEINE Förderdokumente — Programmdaten bleiben leer (rot)")
            meta = FundingMeta()
        else:
            on_progress(f"Programmdaten aus {len(funding_docs)} Förderdokument(en)")
            meta = extract_funding_meta(funding_docs)

    # The EH-vs-EM template choice normally follows the extracted program type; when
    # the documents are missing/unclear the caller may assert it (the program family
    # is user input by CLI design — never the figures).
    if program_hint is not None and meta.program_type is BegProgramType.UNCLEAR:
        meta = meta.model_copy(update={"program_type": program_hint})

    invoices: list[InvoiceDocument] = []
    unreadable: list[tuple[Path, str]] = []
    for c in classified:
        if c.doc_class is not DocClass.INVOICE:
            continue
        on_progress(f"Rechnung: {c.path.name}")
        try:
            invoices.append(extract_invoice(c.path))
        except Exception as exc:  # noqa: BLE001 — one bad file must not kill the run
            unreadable.append((c.path, f"{type(exc).__name__}: {exc}"))
    invoices, duplicates = _dedupe(invoices)

    proofs: list[ProofRecord] = []
    for c in classified:
        if c.doc_class is not DocClass.ZAHLUNGSNACHWEIS:
            continue
        on_progress(f"Zahlungsnachweis: {c.path.name}")
        try:
            for proof in extract_payment_proofs(c.path):
                proofs.append(ProofRecord(source_path=c.path, proof=proof))
        except Exception as exc:  # noqa: BLE001
            unreadable.append((c.path, f"{type(exc).__name__}: {exc}"))

    reconciliations = reconcile_payments(invoices, proofs)

    labels: dict[int, GewerkLabel] = {}
    for inv in invoices:
        on_progress(f"Gewerk: {inv.vendor_name}")
        labels[id(inv)] = label_invoice(inv)

    table = build_table(meta, reconciliations, labels)

    out = output_path or project_dir / f"Kostenzusammenstellung_{project_dir.name}.xlsx"
    write_kostenzusammenstellung(table, out)

    return BegResult(
        meta=meta,
        table=table,
        invoices=invoices,
        duplicates=duplicates,
        reconciliations=reconciliations,
        proofs=proofs,
        unreadable=unreadable,
        ignored=[c for c in classified if c.doc_class in (DocClass.OFFER, DocClass.OTHER)],
        output_path=out,
    )
