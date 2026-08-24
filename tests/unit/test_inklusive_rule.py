"""unflag_inklusive_positions: a 0,00-€ 'inklusive' line is binding scope at no extra
cost, never an optional position. Consultant-approved determinism rule — the LLM flaps
on this flag between otherwise-identical temperature-0 runs (seen live on Eggersmann
1330412 Pos. 5 'Nachbrechbalken … inklusive (6.175,00 EUR)' and Pos. 9)."""

from __future__ import annotations

from decimal import Decimal

from invoice_controller.extract.offer import unflag_inklusive_positions
from invoice_controller.models import Position


def test_zero_priced_inklusive_line_is_unflagged() -> None:
    positions = [
        Position(
            pos="5",
            description="Nachbrechbalken Typ A5R für F 38 inklusive (6.175,00 EUR)",
            line_total_net=Decimal("0.00"),
            optional=True,
            optional_reason="Preis in Klammern",
        ),
    ]
    (p,) = unflag_inklusive_positions(positions)
    assert p.optional is False
    assert p.optional_reason is None


def test_marker_in_optional_reason_alone_is_enough() -> None:
    positions = [
        Position(
            pos="9",
            description="Trichterautomatik",
            line_total_net=Decimal("0.00"),
            optional=True,
            optional_reason="inklusive (503,50 EUR)",
        ),
    ]
    (p,) = unflag_inklusive_positions(positions)
    assert p.optional is False


def test_abbreviated_inkl_matches() -> None:
    positions = [
        Position(
            pos="3",
            description="500 Bh Service inkl.",
            line_total_net=Decimal("0.00"),
            optional=True,
        ),
    ]
    (p,) = unflag_inklusive_positions(positions)
    assert p.optional is False


def test_zero_priced_optional_without_marker_stays_optional() -> None:
    positions = [
        Position(
            pos="7",
            description="Eventualposition Sonderfarbe",
            line_total_net=Decimal("0.00"),
            optional=True,
            optional_reason="Eventualposition",
        ),
    ]
    (p,) = unflag_inklusive_positions(positions)
    assert p.optional is True
    assert p.optional_reason == "Eventualposition"


def test_priced_inklusive_worded_optional_stays_optional() -> None:
    # 'inkl.' inside a PRICED optional line describes its scope, not a freebie.
    positions = [
        Position(
            pos="2",
            description="Garantieverlängerung inkl. Wartung",
            line_total_net=Decimal("8000.00"),
            optional=True,
            optional_reason="Optionalposition",
        ),
    ]
    (p,) = unflag_inklusive_positions(positions)
    assert p.optional is True


def test_mandatory_positions_untouched() -> None:
    positions = [
        Position(
            pos="1",
            description="FORUS F 38 inkl. Elektromotor",
            line_total_net=Decimal("335730.00"),
        ),
    ]
    (p,) = unflag_inklusive_positions(positions)
    assert p.optional is False
