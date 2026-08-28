"""Per-invoice position cross-sum (decided 2026-08-28): every invoice — EEW and
BEG — is extracted position-level and Σ(line items) is verified against the stated
total. Type-aware target (Schlussrechnung → cumulative), brutto-extraction hint,
loud-not-blocking wiring into the VNE compute."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_controller.config import ProjektConfig
from invoice_controller.extract.cross_sum import check_invoice_positions
from invoice_controller.models import (
    AmountCheck,
    CrossSumCheck,
    InvoiceDocument,
    InvoicePosition,
)
from invoice_controller.vne.compute import compute_vne
from invoice_controller.vne.ratios import VendorRatio


def _pos(total: str, desc: str = "Leistung") -> InvoicePosition:
    return InvoicePosition(description=desc, line_total_net=Decimal(total))


def test_positions_match_netto_passes() -> None:
    chk = check_invoice_positions(
        [_pos("60.00"), _pos("40.00")], Decimal("100.00"), brutto=Decimal("119.00")
    )
    assert chk.passed
    assert chk.actual == Decimal("100.00")


def test_positions_mismatch_fails_with_difference() -> None:
    chk = check_invoice_positions([_pos("60.00"), _pos("39.00")], Decimal("100.00"))
    assert not chk.passed
    assert "1.00" in (chk.message or "")


def test_schlussrechnung_positions_match_cumulative() -> None:
    # Positions describe the whole project (83.500) while netto is the remaining
    # amount after deducted advances — the cumulative figure is a valid target.
    chk = check_invoice_positions(
        [_pos("50000.00"), _pos("33500.00")],
        Decimal("13682.77"),
        cumulative_netto=Decimal("83500.00"),
    )
    assert chk.passed
    assert chk.expected == Decimal("83500.00")


def test_gutschrift_negative_positions_match_negative_netto() -> None:
    chk = check_invoice_positions([_pos("-5332.77", "Gutschrift Position")], Decimal("-5332.77"))
    assert chk.passed


def test_discount_line_counts_into_the_sum() -> None:
    chk = check_invoice_positions(
        [_pos("110.00"), _pos("-10.00", "Rabatt")], Decimal("100.00")
    )
    assert chk.passed


def test_brutto_extraction_is_called_out() -> None:
    # Σ(positions) hits the brutto → the message must say the lines were captured gross.
    chk = check_invoice_positions(
        [_pos("119.00")], Decimal("100.00"), brutto=Decimal("119.00")
    )
    assert not chk.passed
    assert "BRUTTO" in (chk.message or "")


def test_empty_positions_fail_loudly() -> None:
    chk = check_invoice_positions([], Decimal("100.00"))
    assert not chk.passed
    assert "keine Positionen" in (chk.message or "")


# --- wiring into the VNE compute --------------------------------------------------------

def _ratio(vendor: str) -> VendorRatio:
    return VendorRatio(
        vendor=vendor, header=f"SOLL: {vendor} Angebot 1 vom 01.01.2026",
        anteil_ik=Decimal("1"), anteil_nk=Decimal("0"),
        beantragt_ik=Decimal("90000"), beantragt_nk=Decimal("0"),
        gesamt=Decimal("90000"), sonderpreis=None, source="test",
    )


def _invoice(position_check: CrossSumCheck | None) -> InvoiceDocument:
    return InvoiceDocument(
        source_path=Path("x.pdf"), vendor_name="MAFAC", invoice_number="1",
        invoice_date=date(2026, 2, 1), netto=Decimal("10000.00"),
        amount_check=AmountCheck(passed=True), position_check=position_check,
    )


def test_failed_position_check_flags_the_row_but_keeps_the_split() -> None:
    chk = check_invoice_positions([_pos("9000.00")], Decimal("10000.00"))
    result = compute_vne([_invoice(chk)], [_ratio("MAFAC")], ProjektConfig())
    row = result.invoices[0]
    assert any("Positions-Kreuzsumme" in f for f in row.flags)
    # Stated netto stays authoritative: the split still happens.
    assert row.ik_amount == Decimal("10000.00")


def test_passed_position_check_adds_no_flag() -> None:
    chk = check_invoice_positions([_pos("10000.00")], Decimal("10000.00"))
    result = compute_vne([_invoice(chk)], [_ratio("MAFAC")], ProjektConfig())
    assert not any("Positions-Kreuzsumme" in f for f in result.invoices[0].flags)


def test_legacy_invoice_without_position_check_adds_no_flag() -> None:
    result = compute_vne([_invoice(None)], [_ratio("MAFAC")], ProjektConfig())
    assert not any("Positions-Kreuzsumme" in f for f in result.invoices[0].flags)
