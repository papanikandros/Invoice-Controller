"""B4 — Zahlungsnachweis reconciliation: the deterministic matching chain.

Extraction is stubbed (vision is exercised live); the chain itself must be fully
predictable: exact brutto > Skonto-adjusted > Re-Nr.-tied mismatch > no proof.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_controller.beg.payments import (
    PaymentProof,
    PaymentStatus,
    ProofRecord,
    reconcile_payments,
)
from invoice_controller.models import AmountCheck, InvoiceDocument


def _invoice(
    vendor: str,
    number: str,
    brutto: str,
    *,
    netto: str | None = None,
    skonto_pct: str | None = None,
    day: int = 1,
) -> InvoiceDocument:
    b = Decimal(brutto)
    return InvoiceDocument(
        source_path=Path(f"{number}.pdf"),
        vendor_name=vendor,
        invoice_number=number,
        invoice_date=date(2026, 3, day),
        netto=Decimal(netto) if netto else (b / Decimal("1.19")).quantize(Decimal("0.01")),
        brutto=b,
        skonto_pct=Decimal(skonto_pct) if skonto_pct else None,
        amount_check=AmountCheck(passed=True),
    )


def _proof(payee: str | None, amount: str | None, *, zweck: str | None = None,
           day: int = 10) -> ProofRecord:
    return ProofRecord(
        source_path=Path("proof.png"),
        proof=PaymentProof(
            payee_name=payee,
            amount=Decimal(amount) if amount else None,
            payment_date=date(2026, 3, day),
            verwendungszweck=zweck,
        ),
    )


class TestAmountRules:
    def test_exact_brutto_match(self) -> None:
        inv = _invoice("WM Maler GmbH", "RE-1001", "19489.23")
        [rec] = reconcile_payments([inv], [_proof("WM Maler GmbH", "19489.23")])
        assert rec.status is PaymentStatus.PAID
        assert rec.bezahlt == Decimal("19489.23")
        assert "am 10.03.2026 bezahlt" in rec.note

    def test_madroch_skonto_case(self) -> None:
        """The corpus ground truth: 19.489,23 billed, 18.904,56 paid = 3 % Skonto
        (bank rounded up a cent — inside the Skonto tolerance)."""
        inv = _invoice("WM Maler GmbH", "RE-1001", "19489.23", skonto_pct="3")
        [rec] = reconcile_payments([inv], [_proof("WM Maler", "18904.56")])
        assert rec.status is PaymentStatus.PAID_SKONTO
        assert rec.skonto_pct_taken == Decimal("3")
        assert "3,00 % Skonto" in rec.note

    def test_skonto_not_applied_without_stated_terms(self) -> None:
        """A discounted payment with NO Skonto terms on the invoice must not be
        explained away — only stated terms count."""
        inv = _invoice("WM Maler GmbH", "RE-1001", "19489.23")
        [rec] = reconcile_payments([inv], [_proof("WM Maler", "18904.56")])
        assert rec.status is PaymentStatus.NO_PROOF

    def test_renr_tied_amount_mismatch_is_flagged(self) -> None:
        inv = _invoice("Zimmerei Huber", "R-2026-77", "10000.00")
        [rec] = reconcile_payments(
            [inv], [_proof("Hubertus Bank", "7500.00", zweck="Abschlag R-2026-77")]
        )
        assert rec.status is PaymentStatus.AMOUNT_MISMATCH
        assert rec.bezahlt == Decimal("7500.00")
        assert "prüfen" in rec.note

    def test_no_proof(self) -> None:
        inv = _invoice("Dachdecker Meyer", "D-1", "5000.00")
        [rec] = reconcile_payments([inv], [])
        assert rec.status is PaymentStatus.NO_PROOF
        assert rec.bezahlt is None
        assert rec.note == "kein Zahlungsnachweis"


class TestMatchingScope:
    def test_wrong_vendor_does_not_match(self) -> None:
        inv = _invoice("Dachdecker Meyer", "D-1", "5000.00")
        [rec] = reconcile_payments([inv], [_proof("Elektro Schulz", "5000.00")])
        assert rec.status is PaymentStatus.NO_PROOF

    def test_renr_match_without_payee(self) -> None:
        """Screenshots sometimes crop the payee — the Re-Nr. in the Zweck still ties
        the transfer to the invoice."""
        inv = _invoice("Dachdecker Meyer", "RG-2026-0042", "5000.00")
        [rec] = reconcile_payments(
            [inv], [_proof(None, "5000.00", zweck="RG 2026 0042 Dank")]
        )
        assert rec.status is PaymentStatus.PAID

    def test_short_invoice_numbers_do_not_false_match(self) -> None:
        inv = _invoice("Meyer", "17", "5000.00")
        [rec] = reconcile_payments(
            [inv], [_proof(None, "5000.00", zweck="Kd-Nr 999917 Miete")]
        )
        assert rec.status is PaymentStatus.NO_PROOF

    def test_proof_consumed_only_once(self) -> None:
        """Two identical invoices, one payment: the earlier invoice gets the proof,
        the later one is honestly unpaid."""
        inv1 = _invoice("Maler GmbH", "A-1", "1000.00", day=1)
        inv2 = _invoice("Maler GmbH", "A-2", "1000.00", day=15)
        recs = reconcile_payments([inv1, inv2], [_proof("Maler GmbH", "1000.00")])
        by_nr = {r.invoice.invoice_number: r for r in recs}
        assert by_nr["A-1"].status is PaymentStatus.PAID
        assert by_nr["A-2"].status is PaymentStatus.NO_PROOF

    def test_exact_beats_skonto_candidate(self) -> None:
        """When two proofs could serve, the exact-brutto one wins."""
        inv = _invoice("Maler GmbH", "A-1", "1000.00", skonto_pct="3")
        recs = reconcile_payments(
            [inv], [_proof("Maler GmbH", "970.00"), _proof("Maler GmbH", "1000.00")]
        )
        assert recs[0].status is PaymentStatus.PAID
        assert recs[0].bezahlt == Decimal("1000.00")

    def test_amountless_proof_never_matches(self) -> None:
        inv = _invoice("Maler GmbH", "A-1", "1000.00")
        [rec] = reconcile_payments([inv], [_proof("Maler GmbH", None)])
        assert rec.status is PaymentStatus.NO_PROOF
