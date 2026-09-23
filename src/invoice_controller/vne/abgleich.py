"""Positionsabgleich — the scope check inside vne-generation (2026-09-21).

The VNE-Tabelle splits each invoice's stated netto by a per-vendor ratio, so a vendor
who invoiced exactly the offered total for a different set of items passes it
silently. This check matches invoice positions to offer positions per vendor and
reports the variance per offer position. It replaced the standalone Kontrollmappe
procedure (user decision 2026-09-21: one process, no second extraction run).

The offer side costs no extra tokens: it is the positions of the verified
Kostenaufstellung.xlsx when one is uploaded, else the offers that vne-generation
extracts live for the ratios anyway. The invoice side is the position-level
extraction vne-generation already performs.

Decisions carried over from R6: any variance ≠ 0 renders red; bundle rows are single
positions; per-vendor matching boundary, many-to-many via aggregation; the LLM only
augments what the deterministic scorer left unmatched or medium. Every match is a
PROPOSAL — the consultant reviews the sheet.

Schlussrechnung folding is reused from the BEG pipeline: when a vendor's
Schlussrechnung states the cumulative total, its Abschläge fold into it — matching
against BOTH would double-count the invoiced side.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from invoice_controller.beg.compute import _fold_advances
from invoice_controller.match.positions import (
    Confidence,
    MatchProposal,
    propose_matches,
)
from invoice_controller.match.vendor import _overlap_score, normalize_vendor
from invoice_controller.models import DocumentKind, InvoiceDocument, OfferDocument, Position
from invoice_controller.vne.ratios import _STATEMENT_BLOCK_RE, vendor_from_header

ProgressFn = Callable[[str], None]

_LLM_CONFIDENCE_FLOOR = 0.55   # an LLM pair below this stays unmatched

SKIPPED_NO_POSITIONS = (
    "Positionsabgleich übersprungen — Angebotspositionen sind nur aus der "
    "Kostenaufstellung.xlsx oder aus Angebots-PDFs lesbar (nicht aus .ods/PDF-Kostenaufstellungen)"
)


@dataclass
class OfferBlock:
    """One vendor's offer side, whatever its source."""

    label: str
    positions: list[Position]
    sonderpreis: Decimal | None = None


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
    positions: list[Position]
    invoices: list[InvoiceDocument]
    sonderpreis: Decimal | None = None
    rows: list[AbgleichRow] = field(default_factory=list)
    extras: list[ExtraLine] = field(default_factory=list)

    @property
    def offered_total(self) -> Decimal:
        return sum((r.offered for r in self.rows if r.offered is not None and not r.offer_position.optional),
                   Decimal(0))

    @property
    def invoiced_total(self) -> Decimal:
        return sum((r.invoiced for r in self.rows), Decimal(0)) + sum(
            (e.amount for e in self.extras), Decimal(0)
        )

    @property
    def missing(self) -> int:
        return sum(1 for r in self.rows if not r.matched and not r.offer_position.optional)

    @property
    def drifted(self) -> int:
        return sum(1 for r in self.rows if r.matched and r.variance not in (None, 0))


@dataclass
class AbgleichResult:
    groups: list[VendorGroup] = field(default_factory=list)
    offerless_invoices: list[InvoiceDocument] = field(default_factory=list)
    offer_source: str = ""              # where the offer positions came from
    skipped_reason: str | None = None   # set when no offer positions were available


# ---------------------------------------------------------------------------
# Offer side
# ---------------------------------------------------------------------------


def blocks_from_offer_documents(offers: list[OfferDocument]) -> list[OfferBlock]:
    """Statements are excluded: an estimate lists no vendor and no binding positions,
    so there is nothing an invoice line could deviate from."""
    return [
        OfferBlock(label=o.header.vendor_name, positions=list(o.positions), sonderpreis=o.totals.sonderpreis)
        for o in offers
        if o.kind is not DocumentKind.STATEMENT
    ]


def blocks_from_kostenaufstellung_xlsx(path: Path) -> list[OfferBlock]:
    """Position rows of the cost-estimation output, as the consultant verified them.
    Mirrors the block walk of `ratios.from_kostenaufstellung_xlsx`; the sheet carries
    no quantity/article columns, so matching leans on description + price."""
    import openpyxl

    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]

    def _dec(v) -> Decimal | None:
        if v is None or isinstance(v, str):
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    blocks: list[OfferBlock] = []
    current: OfferBlock | None = None
    in_positions = False
    for row in ws.iter_rows(min_col=1, max_col=6):
        a, b = row[0].value, row[1].value
        first = str(a).strip() if a is not None else ""
        if first.startswith("SOLL"):
            current, in_positions = None, False
            if not _STATEMENT_BLOCK_RE.search(first):
                current = OfferBlock(label=vendor_from_header(first), positions=[])
                blocks.append(current)
                in_positions = True
            continue
        if current is None:
            continue
        if first.startswith("Σ"):
            in_positions = False
            continue
        if isinstance(b, str) and b.startswith("Sonderpreis"):
            current.sonderpreis = _dec(row[2].value)
            continue
        if not in_positions or not first or first == "Position" or not isinstance(b, str):
            continue
        total = _dec(row[2].value)
        # The writer suffixes "(optional)" unless the wording already says so.
        optional = "option" in b.lower()
        if total is None and not optional:
            continue
        current.positions.append(Position(pos=first, description=b, line_total_net=total, optional=optional))
    return [blk for blk in blocks if blk.positions]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _vendor_key(name: str) -> frozenset[str]:
    return frozenset(normalize_vendor(name))


def _match_group(group: VendorGroup, *, with_llm: bool, on_progress: ProgressFn) -> None:
    offer_positions = [
        pos for pos in group.positions
        if not pos.optional or pos.line_total_net  # optionals with a price still matchable
    ]

    invoices, _folded = _fold_advances(group.invoices)
    inv_positions: list[tuple[InvoiceDocument, int]] = [
        (inv, k) for inv in invoices for k in range(len(inv.positions))
    ]
    flat_inv = [inv.positions[k] for inv, k in inv_positions]

    proposals: list[MatchProposal] = propose_matches(offer_positions, flat_inv)

    # LLM augmentation for the leftovers (one call per vendor, only if needed).
    llm_notes: dict[int, tuple[int | None, str, float]] = {}
    needy = [p.invoice_index for p in proposals if p.confidence is not Confidence.HIGH]
    if with_llm and needy and offer_positions:
        from invoice_controller.match.llm import propose_matches_llm

        on_progress(f"LLM-Abgleich {group.label}: {len(needy)} Position(en)")
        try:
            result = propose_matches_llm(offer_positions, flat_inv, needy)
            for pair in result.pairs:
                if pair.rechnung_nr in needy:
                    llm_notes[pair.rechnung_nr] = (
                        pair.angebot_nr if pair.angebot_nr is not None and 0 <= pair.angebot_nr < len(offer_positions) else None,
                        pair.begruendung,
                        pair.sicherheit,
                    )
        except Exception as exc:  # noqa: BLE001 — augmentation must never kill the build
            on_progress(f"LLM-Abgleich übersprungen ({type(exc).__name__})")

    rows = {id(pos): AbgleichRow(offer_position=pos) for pos in offer_positions}

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
        rows[id(offer_positions[target])].matched.append(
            MatchedLine(invoice=inv, position_index=k, amount=amount,
                        confidence=confidence, source=source, note=note)
        )

    group.rows = [rows[id(pos)] for pos in offer_positions]


def build_abgleich(
    blocks: list[OfferBlock],
    invoices: list[InvoiceDocument],
    *,
    offer_source: str = "",
    with_llm: bool = True,
    on_progress: ProgressFn = lambda _m: None,
) -> AbgleichResult:
    # Per-vendor grouping (PLAN: never match across vendors by default).
    groups: list[VendorGroup] = []
    for block in blocks:
        key = _vendor_key(block.label)
        existing = next((g for g in groups if _overlap_score(_vendor_key(g.label), key) > 0), None)
        if existing is None:
            groups.append(VendorGroup(label=block.label, positions=list(block.positions),
                                      invoices=[], sonderpreis=block.sonderpreis))
        else:
            existing.positions.extend(block.positions)
            # Two offers of one vendor: a single Sonderpreis no longer describes the sum.
            existing.sonderpreis = None

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

    for group in groups:
        on_progress(f"Positionsabgleich: {group.label} ({len(group.invoices)} Rechnung(en))")
        _match_group(group, with_llm=with_llm, on_progress=on_progress)

    return AbgleichResult(groups=groups, offerless_invoices=offerless, offer_source=offer_source)
