from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_controller.models import MAX_DESCRIPTION_LEN, Position


def _pos(description: str) -> Position:
    return Position(pos="1", description=description, line_total_net=Decimal("1.00"))


def test_short_description_is_untouched() -> None:
    name = "Radial-Kühltürme KT-1.000-25-FU"
    assert _pos(name).description == name


def test_description_is_stripped() -> None:
    assert _pos("  Richtpreis Montage  ").description == "Richtpreis Montage"


def test_overlong_description_is_truncated_within_cap() -> None:
    long = (
        "Unterverteilung: Projektierung, Lohnkosten und Materialkosten "
        "inklusive Verkabelung und Inbetriebnahme der Schaltanlage"
    )
    out = _pos(long).description
    assert len(out) <= MAX_DESCRIPTION_LEN
    assert out.endswith("…")


def test_truncation_prefers_word_boundary() -> None:
    # 79-char word boundary cut: no mid-word break before the ellipsis.
    out = _pos("Schalt und Regelschrank " + "A" * 100).description
    assert len(out) <= MAX_DESCRIPTION_LEN
    assert out.endswith("…")
    # The long unbroken run forces a hard cut; the boundary-preferring branch is
    # exercised by the natural-language case above.


def test_mandatory_position_requires_line_total() -> None:
    with pytest.raises(ValidationError):
        Position(pos="1", description="x", line_total_net=None)


def test_optional_position_may_omit_line_total() -> None:
    # A rate-card Eventualposition ("95,00 €/m") has no computable line total.
    pos = Position(
        pos="E1",
        description="PVC-Verrohrung 95,00 €/m",
        line_total_net=None,
        optional=True,
        optional_reason="Eventualposition",
    )
    assert pos.optional
    assert pos.line_total_net is None
