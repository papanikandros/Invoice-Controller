"""Assemble the Kostenzusammenstellung rows (B5).

One row per invoice, grouped by Gewerk within two sections (Maßnahmen /
Baubegleitung & Fachplanung), mirroring the consultant's ground-truth sheets
(EM_Madroch, EH_Fissenewert):

- **Schlussrechnung folding** (Madroch Zimmerei pattern): when a vendor's
  Schlussrechnung states the cumulative project total, its Anzahlungs-/
  Abschlagsrechnungen do NOT get own rows — the Schlussrechnung row carries the
  cumulative Re-Betrag and notes how many advances it folds.
- **Re-Betrag is ALWAYS brutto** (corrected 2026-08-31 against the Buttergasse
  ground truth: the consultant's Re-Betrag column shows brutto even for business
  clients — the netto basis applies to the FÖRDERFÄHIG column, which B3/the
  consultant fills; Unternehmen rows carry a "förderfähig auf Netto-Basis" note).
  A cumulative brutto the document does not state literally is derived
  netto × (1 + MwSt-Satz) and SAID SO in the Anmerkung — never silently.
- **förderfähig stays EMPTY** until the eligibility layer (B3, blocked on the
  consultant's fundability-rules document) or the consultant fills it — the
  Förderung formulas reference the cells live, so the sheet computes as soon as the
  cells are filled. This matches the test contract: judgment cells are asserted
  flagged, not equal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_controller.beg.funding import ClientBasis, FundingMeta
from invoice_controller.beg.gewerk import BegSection, GewerkLabel
from invoice_controller.beg.payments import PaymentStatus, Reconciliation
from invoice_controller.match.vendor import normalize_vendor
from invoice_controller.models import InvoiceDocument, InvoiceType
from invoice_controller.normalize import format_de_decimal


@dataclass
class BegRow:
    section: BegSection
    gewerk: str
    firma: str
    re_nr: str
    re_datum: date
    re_positionen: str                    # "siehe Rechnung" | "alle" (Baubegleitung)
    re_betrag: Decimal | None
    bezahlt: Decimal | None
    foerderfaehig: Decimal | None         # None = consultant judgment pending (B3)
    anmerkung: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # red-rendered reasons
    source_path: Path | None = None

    @property
    def anmerkung_text(self) -> str:
        return "; ".join(self.anmerkung)


@dataclass
class BegTable:
    meta: FundingMeta
    massnahmen: list[BegRow]
    baubegleitung: list[BegRow]
    leistungszeitraum: tuple[date, date] | None

    @property
    def rows(self) -> list[BegRow]:
        return self.massnahmen + self.baubegleitung


def _vendor_key(name: str) -> frozenset[str]:
    return frozenset(normalize_vendor(name))


def _fold_advances(invoices: list[InvoiceDocument]) -> tuple[list[InvoiceDocument], dict[int, int]]:
    """Drop Anzahlungs-/Teilrechnungen — and pre-Schlussrechnung Gutschriften, which
    correct an Abschlag and are already inside the SR's deducted advances (verified
    on ZePa/MAFAC: 33.367,23 + 41.750,00 = the SR's 75.117,23) — whenever the vendor
    has a cumulative Schlussrechnung. Vendor identity via overlap score, NOT exact
    token equality: the same vendor extracts with slightly different name strings
    across documents (the live ZePa finding, 2026-09-03).
    Returns (kept invoices, {id(schlussrechnung) → number of folded documents})."""
    from invoice_controller.match.vendor import _overlap_score

    finals: list[tuple[frozenset[str], InvoiceDocument]] = [
        (_vendor_key(inv.vendor_name), inv)
        for inv in invoices
        if inv.invoice_type is InvoiceType.SCHLUSSRECHNUNG and inv.cumulative_netto is not None
    ]

    kept: list[InvoiceDocument] = []
    folded: dict[int, int] = {}
    for inv in invoices:
        key = _vendor_key(inv.vendor_name)
        final = next(
            (f for fk, f in finals if f is not inv and _overlap_score(set(fk), set(key)) > 0),
            None,
        )
        foldable = inv.invoice_type in (
            InvoiceType.ANZAHLUNGSRECHNUNG, InvoiceType.TEILRECHNUNG
        ) or (
            inv.invoice_type is InvoiceType.GUTSCHRIFT
            and final is not None
            and inv.invoice_date <= final.invoice_date
        )
        if final is not None and foldable:
            folded[id(final)] = folded.get(id(final), 0) + 1
            continue
        kept.append(inv)
    return kept, folded


def _re_betrag(
    inv: InvoiceDocument, basis: ClientBasis, cumulative: bool
) -> tuple[Decimal | None, list[str], list[str]]:
    """The row's Re-Betrag — always brutto (see module docstring). Returns
    (amount, anmerkungen, flags)."""
    notes: list[str] = []
    flags: list[str] = []

    if basis is ClientBasis.UNTERNEHMEN:
        notes.append("förderfähig auf Netto-Basis (Unternehmen)")
    elif basis is ClientBasis.UNCLEAR:
        flags.append("Kundenbasis (privat/Unternehmen) unklar — prüfen")

    if not cumulative:
        if inv.brutto is not None:
            return inv.brutto, notes, flags
        flags.append("Brutto fehlt auf der Rechnung")
        return inv.netto, notes + ["Netto ausgewiesen"], flags

    # Cumulative brutto is rarely printed as such; deriving it is stated openly.
    if inv.mwst_pct is None:
        flags.append("Gesamt-Brutto nicht ermittelbar (MwSt-Satz fehlt)")
        return None, notes, flags
    derived = (inv.cumulative_netto * (1 + inv.mwst_pct / Decimal(100))).quantize(Decimal("0.01"))
    notes.append(
        f"Gesamt-Brutto abgeleitet: {format_de_decimal(inv.cumulative_netto)} netto × "
        f"{format_de_decimal(inv.mwst_pct)} % MwSt"
    )
    return derived, notes, flags


def build_table(
    meta: FundingMeta,
    reconciliations: list[Reconciliation],
    labels: dict[int, GewerkLabel],
) -> BegTable:
    """`labels` is keyed by id(invoice) — the orchestrator labels each invoice once."""
    invoices = [r.invoice for r in reconciliations]
    recon_by_id = {id(r.invoice): r for r in reconciliations}
    kept, folded = _fold_advances(invoices)

    rows: list[BegRow] = []
    for inv in kept:
        recon = recon_by_id[id(inv)]
        label = labels[id(inv)]
        cumulative = id(inv) in folded or (
            inv.invoice_type is InvoiceType.SCHLUSSRECHNUNG and inv.cumulative_netto is not None
        )
        amount, notes, flags = _re_betrag(inv, meta.client_basis, cumulative)

        if id(inv) in folded:
            notes.append(f"inkl. {folded[id(inv)]} Abschlags-/Anzahlungsrechnung(en)")

        # Payment result → bezahlt + Anmerkung/flags (decided 2026-08-28: no proof →
        # bezahlt defaults to the Re-Betrag, loudly).
        bezahlt = recon.bezahlt
        if recon.status is PaymentStatus.NO_PROOF:
            bezahlt = amount
            flags.append("kein Zahlungsnachweis")
        elif recon.status is PaymentStatus.AMOUNT_MISMATCH:
            flags.append(recon.note or "Zahlbetrag weicht ab — prüfen")
        elif recon.note:
            notes.append(recon.note)

        # Extraction-side review flags travel onto the row (loud, never blocking).
        if inv.position_check is not None and not inv.position_check.passed:
            flags.append(f"Positions-Kreuzsumme: {inv.position_check.message}")
        if not inv.amount_check.passed:
            flags.append(f"Beträge: {inv.amount_check.message}")
        if inv.grounding_check is not None and not inv.grounding_check.passed:
            flags.append(f"Betrags-Verankerung: {inv.grounding_check.message}")

        notes.append("förderfähig prüfen")  # B3 pending — judgment cell stays empty

        rows.append(
            BegRow(
                section=label.section,
                gewerk=label.gewerk,
                firma=inv.vendor_name,
                re_nr=inv.invoice_number,
                re_datum=inv.invoice_date,
                re_positionen="alle" if label.section is BegSection.BAUBEGLEITUNG else "siehe Rechnung",
                re_betrag=amount,
                bezahlt=bezahlt,
                foerderfaehig=None,
                anmerkung=notes,
                flags=flags,
                source_path=inv.source_path,
            )
        )

    def _grouped(section: BegSection) -> list[BegRow]:
        picked = [r for r in rows if r.section is section]
        # Stable Gewerk grouping, groups ordered by first appearance (invoice date).
        picked.sort(key=lambda r: r.re_datum)
        order: dict[str, int] = {}
        for r in picked:
            order.setdefault(r.gewerk, len(order))
        picked.sort(key=lambda r: (order[r.gewerk], r.re_datum, r.re_nr))
        return picked

    dates = [r.re_datum for r in rows]
    return BegTable(
        meta=meta,
        massnahmen=_grouped(BegSection.MASSNAHME),
        baubegleitung=_grouped(BegSection.BAUBEGLEITUNG),
        leistungszeitraum=(min(dates), max(dates)) if dates else None,
    )
