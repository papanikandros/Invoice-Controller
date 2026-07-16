from __future__ import annotations

from decimal import Decimal

from invoice_controller.extract.cross_sum import check_offer
from invoice_controller.models import Kostenkategorie, OfferTotals, Position


def _pos(total: str, pos: str = "1") -> Position:
    return Position(
        pos=pos,
        description="x",
        line_total_net=Decimal(total),
        kategorie=Kostenkategorie.INVESTITIONSKOSTEN,
    )


def _opt(total: str | None, pos: str = "O") -> Position:
    return Position(
        pos=pos,
        description="opt",
        line_total_net=Decimal(total) if total is not None else None,
        optional=True,
        optional_reason="optionale Position",
        kategorie=Kostenkategorie.INVESTITIONSKOSTEN,
    )


def test_cross_sum_passes_on_exact_match() -> None:
    positions = [_pos("18759.80", "1"), _pos("89880.20", "2")]
    totals = OfferTotals(nettosumme=Decimal("108640.00"))
    result = check_offer(positions, totals)
    assert result.passed
    assert result.actual == Decimal("108640.00")
    assert result.expected == Decimal("108640.00")


def test_cross_sum_passes_within_tolerance() -> None:
    positions = [_pos("100.00"), _pos("0.01")]
    totals = OfferTotals(nettosumme=Decimal("100.00"))
    result = check_offer(positions, totals, tolerance=Decimal("0.02"))
    assert result.passed


def test_cross_sum_fails_outside_tolerance() -> None:
    positions = [_pos("100.00"), _pos("50.00")]
    totals = OfferTotals(nettosumme=Decimal("108.00"))
    result = check_offer(positions, totals)
    assert not result.passed
    assert "42" in (result.message or "")


def test_cross_sum_fails_when_no_nettosumme() -> None:
    positions = [_pos("100.00")]
    totals = OfferTotals()
    result = check_offer(positions, totals)
    assert not result.passed
    assert "Nettosumme" in (result.message or "")


def test_optional_outside_stated_total_passes_via_mandatory() -> None:
    # GranuTrack-style: Nettosumme excludes the optional lines.
    positions = [_pos("31970.00", "1"), _opt("350.00", "5"), _opt("2600.00", "6")]
    totals = OfferTotals(nettosumme=Decimal("31970.00"))
    result = check_offer(positions, totals)
    assert result.passed
    assert result.actual == Decimal("31970.00")
    assert result.actual_incl_optional == Decimal("34920.00")


def test_optional_inside_stated_total_passes_via_with_optional() -> None:
    # L&R-style: Mehrpreis surcharges flagged optional but folded into the Gesamtpreis.
    positions = [_pos("300000.00", "1"), _opt("4800.00", "17"), _opt("8750.00", "18")]
    totals = OfferTotals(nettosumme=Decimal("313550.00"))
    result = check_offer(positions, totals)
    assert result.passed
    assert result.actual_incl_optional == Decimal("313550.00")


def test_rate_card_optional_without_total_is_ignored() -> None:
    positions = [_pos("100.00"), _opt(None, "E1")]
    totals = OfferTotals(nettosumme=Decimal("100.00"))
    result = check_offer(positions, totals)
    assert result.passed


def test_cross_sum_fails_when_neither_interpretation_matches() -> None:
    positions = [_pos("100.00"), _opt("50.00", "O")]
    totals = OfferTotals(nettosumme=Decimal("200.00"))
    result = check_offer(positions, totals)
    assert not result.passed
    assert "mandatory" in (result.message or "")
