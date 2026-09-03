"""R6 — Kontrollmappe builder: offers + invoices → per-vendor position matching.

The original project vision (PLAN.md §Kontrollmappe): extract both sides, match
invoice positions to offer positions per vendor, compute per-position variance.
Decisions applied: Q1 any variance ≠ 0 renders red; Q2 lean sheet (no checkbox
column); Q5 bundle rows are single positions; per-vendor matching boundary,
many-to-many via aggregation; the LLM (MatchGPT patterns) only augments what the
deterministic scorer left unmatched or medium.

Schlussrechnung folding is reused from the BEG pipeline: when a vendor's
Schlussrechnung states the cumulative total, its Abschläge fold into it — matching
against BOTH would double-count the invoiced side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

from invoice_controller.beg.compute import _fold_advances
from invoice_controller.config import ProjektConfig
from invoice_controller.extract.classify import Classified, DocClass, classify_folder
from invoice_controller.extract.invoice import extract_invoice
from invoice_controller.extract.offer import extract_offer
from invoice_controller.match.positions import (
    Confidence,
    MatchProposal,
    propose_matches,
)
from invoice_controller.match.vendor import _overlap_score, normalize_vendor
from invoice_controller.models import InvoiceDocument, OfferDocument, Position

ProgressFn = Callable[[str], None]

_LLM_CONFIDENCE_FLOOR = 0.55   # an LLM pair below this stays unmatched


@dataclass
class MatchedLine:
    invoice: InvoiceDocument
    position_index: int
    amount: Decimal
    confidence: Confidence
    source: str                 # "signal" | "llm"
    note: str = ""              # LLM Begründung or signal summary


@dataclass
class AbgleichRow:
    offer: OfferDocument
    offer_position: Position
    matched: list[MatchedLine] = field(default_factory=list)

    @property
    def offered(self) -> Decimal | None:
        return self.offer_position.line_total_net

    @property
    def invoiced(self) -> Decimal:
        return sum((m.amount for m in self.matched), Decimal(0))

    @property
    def variance(self) -> Decimal | None:
        if self.offered is None:
            return None
        return self.invoiced - self.offered


@dataclass
class ExtraLine:
    """Invoice position with no offer counterpart — flagged `extra` (never dropped)."""

    invoice: InvoiceDocument
    position_index: int
    amount: Decimal
    note: str = ""


@dataclass
class VendorGroup:
    label: str
    offers: list[OfferDocument]
    invoices: list[InvoiceDocument]
    rows: list[AbgleichRow] = field(default_factory=list)
    extras: list[ExtraLine] = field(default_factory=list)

    @property
    def offered_total(self) -> Decimal:
        return sum((r.offered for r in self.rows if r.offered is not None), Decimal(0))

    @property
    def invoiced_total(self) -> Decimal:
        return sum((r.invoiced for r in self.rows), Decimal(0)) + sum(
            (e.amount for e in self.extras), Decimal(0)
        )


@dataclass
class KontrollmappeResult:
    config: ProjektConfig
    groups: list[VendorGroup]
    offerless_invoices: list[InvoiceDocument]     # vendors with no offer at all
    ignored: list[Classified]
    unreadable: list[tuple[Path, str]]
    flags: list[str]
    output_path: Path | None = None


def _vendor_key(name: str) -> frozenset[str]:
    return frozenset(normalize_vendor(name))


def _match_group(group: VendorGroup, *, with_llm: bool, on_progress: ProgressFn) -> None:
    offer_positions: list[tuple[OfferDocument, Position]] = [
        (offer, pos)
        for offer in group.offers
        for pos in offer.positions
        if not pos.optional or pos.line_total_net  # optionals with a price still matchable
    ]
    flat_offer = [pos for _o, pos in offer_positions]

    invoices, _folded = _fold_advances(group.invoices)
    inv_positions: list[tuple[InvoiceDocument, int]] = [
        (inv, k) for inv in invoices for k in range(len(inv.positions))
    ]
    flat_inv = [inv.positions[k] for inv, k in inv_positions]

    proposals: list[MatchProposal] = propose_matches(flat_offer, flat_inv)

    # LLM augmentation for the leftovers (one call per vendor, only if needed).
    llm_notes: dict[int, tuple[int | None, str, float]] = {}
    needy = [p.invoice_index for p in proposals if p.confidence is not Confidence.HIGH]
    if with_llm and needy and flat_offer:
        from invoice_controller.match.llm import propose_matches_llm

        on_progress(f"LLM-Abgleich {group.label}: {len(needy)} Position(en)")
        try:
            result = propose_matches_llm(flat_offer, flat_inv, needy)
            for pair in result.pairs:
                if pair.rechnung_nr in needy:
                    llm_notes[pair.rechnung_nr] = (
                        pair.angebot_nr if pair.angebot_nr is not None and 0 <= pair.angebot_nr < len(flat_offer) else None,
                        pair.begruendung,
                        pair.sicherheit,
                    )
        except Exception as exc:  # noqa: BLE001 — augmentation must never kill the build
            on_progress(f"LLM-Abgleich übersprungen ({type(exc).__name__})")

    rows = {id(pos): AbgleichRow(offer=off, offer_position=pos) for off, pos in offer_positions}

    for proposal in proposals:
        inv, k = inv_positions[proposal.invoice_index]
        amount = flat_inv[proposal.invoice_index].line_total_net
        target: int | None = proposal.offer_index
        confidence, source, note = proposal.confidence, "signal", ""

        llm = llm_notes.get(proposal.invoice_index)
        if llm is not None:
            llm_target, begruendung, sicherheit = llm
            if proposal.confidence is Confidence.NONE:
                if llm_target is not None and sicherheit >= _LLM_CONFIDENCE_FLOOR:
                    target, confidence, source, note = llm_target, Confidence.MEDIUM, "llm", begruendung
                else:
                    note = begruendung
            elif proposal.confidence is Confidence.MEDIUM:
                if llm_target == proposal.offer_index:
                    note = begruendung        # LLM agrees — keep tier, add reasoning
                elif llm_target is not None and sicherheit >= _LLM_CONFIDENCE_FLOOR:
                    target, source, note = llm_target, "llm", begruendung

        if target is None:
            group.extras.append(ExtraLine(invoice=inv, position_index=k, amount=amount, note=note))
            continue
        offer_doc, offer_pos = offer_positions[target]
        rows[id(offer_pos)].matched.append(
            MatchedLine(invoice=inv, position_index=k, amount=amount,
                        confidence=confidence, source=source, note=note)
        )

    group.rows = [rows[id(pos)] for _o, pos in offer_positions]


def build_kontrollmappe(
    project_dir: Path,
    config: ProjektConfig | None = None,
    *,
    with_llm: bool = True,
    classified: list[Classified] | None = None,
    on_progress: ProgressFn = lambda _m: None,
) -> KontrollmappeResult:
    config = config or ProjektConfig()
    # A1: a configured client is masked out of every text-path LLM payload.
    mask = (config.client.name, config.client.address) if config.client else None
    if classified is None:
        classified = classify_folder(project_dir)

    offers: list[OfferDocument] = []
    invoices: list[InvoiceDocument] = []
    unreadable: list[tuple[Path, str]] = []
    for c in classified:
        try:
            if c.doc_class is DocClass.OFFER:
                on_progress(f"Angebot: {c.path.name}")
                offers.append(extract_offer(c.path, with_narrative=False, mask=mask))
            elif c.doc_class is DocClass.INVOICE:
                on_progress(f"Rechnung: {c.path.name}")
                invoices.append(extract_invoice(c.path, mask=mask))
        except Exception as exc:  # noqa: BLE001
            unreadable.append((c.path, f"{type(exc).__name__}: {exc}"))

    flags: list[str] = []
    if not offers:
        flags.append("keine Angebote gefunden — Abgleich nicht möglich, nur Rechnungsliste")

    # Per-vendor grouping (PLAN: never match across vendors by default).
    groups: list[VendorGroup] = []
    for offer in offers:
        key = _vendor_key(offer.header.vendor_name)
        existing = next(
            (g for g in groups if _overlap_score(_vendor_key(g.label), key) > 0), None
        )
        if existing is None:
            groups.append(VendorGroup(label=offer.header.vendor_name, offers=[offer], invoices=[]))
        else:
            existing.offers.append(offer)

    offerless: list[InvoiceDocument] = []
    for inv in invoices:
        key = _vendor_key(inv.vendor_name)
        best, best_score = None, 0.0
        for g in groups:
            s = _overlap_score(_vendor_key(g.label), key)
            if s > best_score:
                best, best_score = g, s
        if best is None or best_score <= 0:
            offerless.append(inv)
            continue
        best.invoices.append(inv)

    if offerless:
        flags.append(
            f"{len(offerless)} Rechnung(en) ohne Angebots-Lieferant — als 'extra' gelistet: "
            + ", ".join(sorted({i.vendor_name for i in offerless}))
        )

    for group in groups:
        on_progress(f"Abgleich: {group.label} ({len(group.invoices)} Rechnung(en))")
        _match_group(group, with_llm=with_llm, on_progress=on_progress)

    return KontrollmappeResult(
        config=config,
        groups=groups,
        offerless_invoices=offerless,
        ignored=[c for c in classified if c.doc_class not in (DocClass.OFFER, DocClass.INVOICE)],
        unreadable=unreadable,
        flags=flags,
    )
