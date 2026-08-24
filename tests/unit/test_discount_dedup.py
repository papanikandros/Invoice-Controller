"""drop_duplicate_discount_positions: the R3b safety net against a document-level
discount being returned BOTH as a pseudo-position and in totals (seen live on the
Prall-Tec AN26-00376 offer, where 'Rechnungsrabatt −119.000,01' came back as a position
while totals also carried the discount)."""

from __future__ import annotations

from decimal import Decimal

from invoice_controller.extract.offer import drop_duplicate_discount_positions
from invoice_controller.models import Kostenkategorie, OfferTotals, Position


def _pos(pos: str, description: str, total: str, **kwargs) -> Position:
    return Position(pos=pos, description=description, line_total_net=Decimal(total), **kwargs)


def test_pseudo_discount_position_dropped_when_preisnachlass_matches() -> None:
    positions = [
        _pos("1", "PTPVS4 Glasschredder", "519000.00"),
        _pos("Rechnungsrabatt", "Rechnungsrabatt", "-119000.01", kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("519000.00"), preisnachlass=Decimal("119000.01"))
    kept = drop_duplicate_discount_positions(positions, totals)
    assert [p.pos for p in kept] == ["1"]


def test_pseudo_discount_position_dropped_when_sonderpreis_differential_matches() -> None:
    positions = [
        _pos("1", "PTPVS4 Glasschredder", "519000.00"),
        _pos("Rechnungsrabatt", "Rechnungsrabatt", "-119000.01", kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("519000.00"), sonderpreis=Decimal("399999.99"))
    kept = drop_duplicate_discount_positions(positions, totals)
    assert [p.pos for p in kept] == ["1"]


def test_discount_position_kept_when_totals_do_not_carry_it() -> None:
    # Not duplicated → numerically consistent as a position; must not be dropped.
    positions = [
        _pos("1", "PTPVS4 Glasschredder", "519000.00"),
        _pos("Rechnungsrabatt", "Rechnungsrabatt", "-119000.01", kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("399999.99"))
    kept = drop_duplicate_discount_positions(positions, totals)
    assert len(kept) == 2


def test_numbered_negative_position_is_never_dropped() -> None:
    # A genuine negative position with its own Pos. number (Eggersmann pos 11
    # 'Entfall Magnetvorbereitung') must survive even when an unrelated document-level
    # discount of the same magnitude exists.
    positions = [
        _pos("1", "FORUS F 38", "335730.00"),
        _pos("11", "Entfall Magnetvorbereitung und Halterung", "-2000.00", kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("333730.00"), preisnachlass=Decimal("2000.00"))
    kept = drop_duplicate_discount_positions(positions, totals)
    assert len(kept) == 2


def test_non_discount_label_is_never_dropped() -> None:
    # Non-numeric pos + negative amount alone is not enough: the label must look like
    # a discount ("Rabatt"/"Nachlass"/"Skonto").
    positions = [
        _pos("1", "Anlage", "10000.00"),
        _pos("Z", "Gutschrift Altgerät", "-500.00", kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("9500.00"), preisnachlass=Decimal("500.00"))
    kept = drop_duplicate_discount_positions(positions, totals)
    assert len(kept) == 2


def test_amount_mismatch_is_never_dropped() -> None:
    positions = [
        _pos("1", "Anlage", "10000.00"),
        _pos("Rabatt", "Rabatt", "-400.00", kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("10000.00"), preisnachlass=Decimal("500.00"))
    kept = drop_duplicate_discount_positions(positions, totals)
    assert len(kept) == 2
