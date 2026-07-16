from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_controller.extract.cross_sum import check_offer
from invoice_controller.models import (
    Kostenkategorie,
    OfferDocument,
    OfferHeader,
    OfferTotals,
    Position,
)
from invoice_controller.template.ods import write_kostenaufstellung
from tests.ods_inspect import read_kostenaufstellung


def _make_offer(tmp_path: Path) -> OfferDocument:
    header = OfferHeader(
        vendor_name="atb Elektronische Steuerungen GmbH",
        offer_number="2400104",
        offer_date=date(2024, 4, 8),
        title="Netzanschluss",
    )
    positions = [
        Position(
            pos="1",
            description="Unterverteilung: Projektierung, Lohnkosten, Materialkosten",
            line_total_net=Decimal("18759.80"),
            kategorie=Kostenkategorie.NEBENKOSTEN,
        ),
        Position(
            pos="2",
            description="Richtpreis Montage",
            line_total_net=Decimal("89880.20"),
            kategorie=Kostenkategorie.INVESTITIONSKOSTEN,
        ),
    ]
    totals = OfferTotals(nettosumme=Decimal("108640.00"))
    cross_sum = check_offer(positions, totals)
    return OfferDocument(
        source_path=tmp_path / "atb.pdf",
        header=header,
        positions=positions,
        totals=totals,
        cross_sum=cross_sum,
    )


def test_kostenaufstellung_roundtrips(tmp_path: Path) -> None:
    offer = _make_offer(tmp_path)
    out = tmp_path / "out.ods"
    write_kostenaufstellung([offer], out)

    blocks = read_kostenaufstellung(out)
    assert len(blocks) == 1
    block = blocks[0]
    assert "atb" in block.soll_header
    assert "2400104" in block.soll_header
    assert len(block.rows_positions) == 2

    total_col = 2  # Gesamtkosten
    inv_col = 3
    neb_col = 4

    p1_row = block.rows_positions[0]
    assert p1_row.value_at(total_col) == Decimal("18759.8")
    assert p1_row.value_at(neb_col) == Decimal("18759.8")
    assert p1_row.value_at(inv_col) == Decimal("0")

    p2_row = block.rows_positions[1]
    assert p2_row.value_at(total_col) == Decimal("89880.2")
    assert p2_row.value_at(inv_col) == Decimal("89880.2")

    assert block.row_sum is not None
    sum_cell = block.row_sum.cells[total_col]
    assert sum_cell.formula and "SUM" in sum_cell.formula, (
        f"Σ row Gesamtkosten cell must contain a SUM formula, got {sum_cell.formula!r}"
    )


def _make_offer_with_optionals(tmp_path: Path) -> OfferDocument:
    header = OfferHeader(
        vendor_name="Pressure Company",
        offer_number="R-1",
        offer_date=date(2025, 11, 28),
        title="Druckluftstation",
    )
    positions = [
        Position(
            pos="1",
            description="BOGE S 160-4 Schraubenkompressor",
            line_total_net=Decimal("190500.00"),
            kategorie=Kostenkategorie.INVESTITIONSKOSTEN,
        ),
        Position(
            pos="2",
            description="BOGE S 38-4 LF Schraubenkompressor",
            line_total_net=Decimal("23700.00"),
            optional=True,
            optional_reason="Preis in Klammern",
            kategorie=Kostenkategorie.INVESTITIONSKOSTEN,
        ),
        Position(
            pos="E1",
            description="PVC-Verrohrung 95,00 €/m",
            line_total_net=None,
            optional=True,
            optional_reason="Eventualposition",
            kategorie=Kostenkategorie.INVESTITIONSKOSTEN,
        ),
    ]
    totals = OfferTotals(nettosumme=Decimal("190500.00"))
    cross_sum = check_offer(positions, totals)
    return OfferDocument(
        source_path=tmp_path / "reflex.pdf",
        header=header,
        positions=positions,
        totals=totals,
        cross_sum=cross_sum,
    )


def test_optional_positions_render_inline_tagged_and_in_sum(tmp_path: Path) -> None:
    offer = _make_offer_with_optionals(tmp_path)
    out = tmp_path / "out.ods"
    write_kostenaufstellung([offer], out)

    block = read_kostenaufstellung(out)[0]
    total_col = 2
    assert len(block.rows_positions) == 3

    # Priced optional is tagged inline and keeps its value.
    priced_opt = block.rows_positions[1]
    assert "(optional)" in priced_opt.text_at(1)
    assert priced_opt.value_at(total_col) == Decimal("23700")

    # Rate-card optional: tagged, rate kept in description, no Gesamtkosten value.
    rate_opt = block.rows_positions[2]
    assert "(optional)" in rate_opt.text_at(1)
    assert "95,00 €/m" in rate_opt.text_at(1)
    assert rate_opt.value_at(total_col) is None

    # The priced optional is included in the Σ (user's chosen layout).
    assert block.row_sum is not None
    assert block.row_sum.value_at(total_col) == Decimal("214200")
