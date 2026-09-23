"""VNE computation — pure, deterministic, fully unit-testable. No I/O, no LLM.

Implements the sheet semantics recovered from the close-out corpus:

  netto_nach_skonto = netto × (1 − skonto_rate)         (rate 0 in v1; consultant edits)
  betrag_C          = netto_nach_skonto × anteil_C      per category C ∈ {IK, NK, EK}
  ansetzbar_C       = MIN(beantragt_C, Σ betrag_C)
  Mehrkosten        = Σ gesamt − AGVO-Referenzkosten
  max. Förderbetrag = Mehrkosten × Kostendeckel-Förderanteil, capped by the
                      Bescheid-Förderbetrag ("lower of")

beantragt IK/NK come from the cost-estimation blocks; beantragt EK = Σ netto of the
consultant's own invoices (decided 2026-08-24). An invoice whose vendor matches
no cost-estimation block gets NO split — flagged, cells left empty for the consultant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from invoice_controller.config import ProjektConfig
from invoice_controller.match.vendor import is_own_company, match_vendor, normalize_vendor
from invoice_controller.models import InvoiceDocument, InvoiceType
from invoice_controller.vne.ratios import VendorRatio, is_statement_block

if TYPE_CHECKING:
    from invoice_controller.vne.abgleich import AbgleichResult

_CENT = Decimal("0.01")


@dataclass
class SplitRow:
    category: str          # "IK" | "NK" | "EK"
    anteil: Decimal
    amount: Decimal        # netto_nach_skonto × anteil, rounded to cents


@dataclass
class VneRow:
    invoice: InvoiceDocument
    vendor_ratio: VendorRatio | None
    own_company: bool
    window_ok: bool | None          # None = no window configured → column stays blank
    skonto_rate: Decimal
    netto_after_skonto: Decimal
    splits: list[SplitRow] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    # Duplicate or failed extraction: rendered for review but excluded from every
    # aggregate (splits, beantragt EK, vendor reconciliation).
    unusable: bool = False

    def _amount(self, cat: str) -> Decimal:
        return sum((s.amount for s in self.splits if s.category == cat), Decimal(0))

    @property
    def ik_amount(self) -> Decimal:
        return self._amount("IK")

    @property
    def nk_amount(self) -> Decimal:
        return self._amount("NK")

    @property
    def ek_amount(self) -> Decimal:
        return self._amount("EK")


@dataclass
class VendorSummary:
    """Per-vendor reconciliation: what was invoiced vs what the offer said."""

    vendor: str
    offer_gesamt: Decimal | None
    offer_sonderpreis: Decimal | None
    invoiced_netto: Decimal
    variance: Decimal | None        # invoiced − (sonderpreis or gesamt)


@dataclass
class VneResult:
    invoices: list[VneRow]
    ignored: list[Path]             # `other`-classified documents, reported by name
    ratio_source: str
    vendor_summaries: list[VendorSummary] = field(default_factory=list)
    beantragt_ik: Decimal | None = None
    beantragt_nk: Decimal | None = None
    beantragt_ek: Decimal | None = None
    # Förderbetrag chain (None when projekt.yaml lacks the Bescheid figures)
    mehrkosten: Decimal | None = None
    max_foerderbetrag: Decimal | None = None
    foerderbetrag_tatsaechlich: Decimal | None = None
    # Scope check (vne/abgleich.py), attached by the orchestrator after compute_vne —
    # it never feeds back into any figure above.
    abgleich: AbgleichResult | None = None

    @property
    def sum_ik(self) -> Decimal:
        return sum((r.ik_amount for r in self.invoices), Decimal(0))

    @property
    def sum_nk(self) -> Decimal:
        return sum((r.nk_amount for r in self.invoices), Decimal(0))

    @property
    def sum_ek(self) -> Decimal:
        return sum((r.ek_amount for r in self.invoices), Decimal(0))

    @property
    def sum_total(self) -> Decimal:
        return self.sum_ik + self.sum_nk + self.sum_ek

    def ansetzbar(self, cat: str) -> Decimal | None:
        actual = {"IK": self.sum_ik, "NK": self.sum_nk, "EK": self.sum_ek}[cat]
        beantragt = {"IK": self.beantragt_ik, "NK": self.beantragt_nk, "EK": self.beantragt_ek}[cat]
        if beantragt is None:
            return None
        return min(beantragt, actual)


def _address_matches(recipient: str | None, client_name: str, client_address: str | None) -> bool | None:
    """Fuzzy token check of the invoice's billed party against the configured client.
    None when the invoice carries no recipient (nothing to check, flag separately)."""
    if not recipient:
        return None
    want = normalize_vendor(client_name)
    if client_address:
        want |= normalize_vendor(client_address)
    got = normalize_vendor(recipient)
    return bool(want & got)


def compute_vne(
    invoices: list[InvoiceDocument],
    ratios: list[VendorRatio],
    config: ProjektConfig,
    *,
    ignored: list[Path] | None = None,
    ratio_source: str = "",
) -> VneResult:
    rows: list[VneRow] = []
    # Duplicate detection: project folders sometimes hold the same invoice twice
    # under different filenames (seen live: Dannemann, Jacob, Presspart). The first
    # occurrence is booked; later ones flag as duplicates with NO split so sums
    # are never double-counted — the consultant confirms and deletes.
    seen_first: dict[tuple[str, Decimal], InvoiceDocument] = {}
    for inv in invoices:
        own = is_own_company(inv.vendor_name)
        ratio = None if own else match_vendor(inv.vendor_name, ratios)
        skonto_rate = Decimal(0)  # actual Skonto taken is not on the invoice; consultant edits
        netto_after = (inv.netto * (1 - skonto_rate)).quantize(_CENT)

        flags: list[str] = []
        unusable = False

        dup_key = (inv.invoice_number.strip().lower(), inv.netto)
        first = seen_first.get(dup_key)
        if first is not None and (normalize_vendor(first.vendor_name) & normalize_vendor(inv.vendor_name)):
            flags.append(
                f"mögliches Duplikat von {first.source_path.name} — nicht aufgeteilt, manuell prüfen"
            )
            unusable = True
        else:
            seen_first.setdefault(dup_key, inv)

        if inv.netto == 0:
            # A 0-€ invoice is almost always a failed extraction (bad scan) — a real
            # invoice states an amount. Loud marker instead of a plausible-looking row.
            flags.append("Extraktion unbrauchbar (Netto = 0 €) — Rechnung manuell erfassen")
            unusable = True

        if not inv.amount_check.passed:
            flags.append(f"Betragsprüfung: {inv.amount_check.message}")
        if inv.position_check is not None and not inv.position_check.passed:
            # Position cross-sum failed (2026-08-28 rule): stated netto stays
            # authoritative for the split, but the row must say the line items
            # don't reconcile — like a failed offer cross-sum, loud not blocking.
            flags.append(f"Positions-Kreuzsumme: {inv.position_check.message}")
        if not own and ratio is None:
            # No vendor block — but a single SCHÄTZUNG block covers exactly the
            # invoices that never had an offer: apply its ratio, red-flagged for
            # review (decided 2026-08-25). Ambiguous (several statements) or absent
            # → stay unallocated.
            statements = [r for r in ratios if is_statement_block(r)]
            if len(statements) == 1:
                ratio = statements[0]
                flags.append("kein Angebot — Aufteilung lt. Schätzung — prüfen")
            else:
                flags.append("kein Angebot in der Kostenaufstellung — manuell kategorisieren")

        window_ok = config.window_ok(inv.invoice_date)
        if window_ok is False:
            flags.append("Rechnungsdatum außerhalb des Bewilligungszeitraums")

        if config.client is not None:
            # A1 (2026-09-03): on a masked run the deterministic pre-masking check is
            # authoritative — the LLM never saw the recipient. Fallback: LLM fields.
            if inv.recipient_local_ok is not None:
                addr_ok = inv.recipient_local_ok
            else:
                addr_ok = _address_matches(
                    inv.recipient_name if inv.recipient_address is None
                    else f"{inv.recipient_name or ''} {inv.recipient_address}",
                    config.client.name,
                    config.client.address,
                )
            if addr_ok is False:
                flags.append(f"Rechnungsempfänger {inv.recipient_name!r} ≠ Antragsteller")
            elif addr_ok is None:
                flags.append("kein Rechnungsempfänger erkennbar — Adresse manuell prüfen")

        splits: list[SplitRow] = []
        if unusable:
            pass  # duplicates and failed extractions get no split — cells stay empty
        elif own:
            splits.append(SplitRow("EK", Decimal(1), netto_after))
        elif ratio is not None:
            if ratio.anteil_ik > 0:
                splits.append(
                    SplitRow("IK", ratio.anteil_ik, (netto_after * ratio.anteil_ik).quantize(_CENT))
                )
            if ratio.anteil_nk > 0:
                splits.append(
                    SplitRow("NK", ratio.anteil_nk, (netto_after * ratio.anteil_nk).quantize(_CENT))
                )

        rows.append(
            VneRow(
                invoice=inv,
                vendor_ratio=ratio,
                own_company=own,
                window_ok=window_ok,
                skonto_rate=skonto_rate,
                netto_after_skonto=netto_after,
                splits=splits,
                flags=flags,
                unusable=unusable,
            )
        )

    result = VneResult(
        invoices=rows,
        ignored=ignored or [],
        ratio_source=ratio_source,
    )

    # Per-vendor reconciliation + Anzahlungskette consistency.
    by_ratio: dict[int, list[VneRow]] = {}
    for row in rows:
        if row.vendor_ratio is not None and not row.unusable:
            by_ratio.setdefault(id(row.vendor_ratio), []).append(row)
    for group in by_ratio.values():
        ratio = group[0].vendor_ratio
        assert ratio is not None
        invoiced = sum((r.invoice.netto for r in group), Decimal(0))
        target = ratio.sonderpreis if ratio.sonderpreis is not None else ratio.gesamt
        result.vendor_summaries.append(
            VendorSummary(
                vendor=ratio.vendor,
                offer_gesamt=ratio.gesamt,
                offer_sonderpreis=ratio.sonderpreis,
                invoiced_netto=invoiced,
                variance=(invoiced - target) if target is not None else None,
            )
        )
        for row in group:
            inv = row.invoice
            if inv.invoice_type is InvoiceType.SCHLUSSRECHNUNG and inv.deducted_advances_netto is not None:
                # Gutschriften belong in the advances sum: a credit note reduces what
                # the Schlussrechnung deducts (seen live on ZePa/MAFAC).
                advances = sum(
                    (
                        r.invoice.netto
                        for r in group
                        if r.invoice.invoice_type
                        in (
                            InvoiceType.ANZAHLUNGSRECHNUNG,
                            InvoiceType.TEILRECHNUNG,
                            InvoiceType.GUTSCHRIFT,
                        )
                    ),
                    Decimal(0),
                )
                if abs(advances - inv.deducted_advances_netto) > Decimal("0.02"):
                    row.flags.append(
                        f"Anzahlungskette: Σ Anzahlungen/Abschläge {advances} ≠ "
                        f"lt. Schlussrechnung abgezogene {inv.deducted_advances_netto}"
                    )

    # beantragt: IK/NK from the cost-estimation blocks, EK = Σ own invoices (decided 2026-08-24).
    # A consultant-own block in the Kostenaufstellung (e.g. a summary section the
    # PDF parser mistook for a vendor) must not inflate the vendor sums.
    vendor_blocks = [r for r in ratios if not is_own_company(r.vendor)]
    if vendor_blocks:
        result.beantragt_ik = sum(
            (r.beantragt_ik for r in vendor_blocks if r.beantragt_ik is not None), Decimal(0)
        )
        result.beantragt_nk = sum(
            (r.beantragt_nk for r in vendor_blocks if r.beantragt_nk is not None), Decimal(0)
        )
    own_rows = [r for r in rows if r.own_company and not r.unusable]
    if own_rows:
        result.beantragt_ek = sum((r.invoice.netto for r in own_rows), Decimal(0))

    # Förderbetrag chain — only with the Bescheid figures from projekt.yaml.
    b = config.bescheid
    if b is not None and b.agvo_referenzkosten is not None:
        result.mehrkosten = result.sum_total - b.agvo_referenzkosten
    elif b is not None:
        result.mehrkosten = result.sum_total
    if b is not None and b.kostendeckel_foerderanteil is not None and result.mehrkosten is not None:
        result.max_foerderbetrag = (result.mehrkosten * b.kostendeckel_foerderanteil).quantize(_CENT)
        if b.foerderbetrag is not None:
            result.foerderbetrag_tatsaechlich = min(result.max_foerderbetrag, b.foerderbetrag)

    return result
