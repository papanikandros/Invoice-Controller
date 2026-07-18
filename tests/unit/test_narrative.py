from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from odfdo import Document, Table

from invoice_controller.extract.cross_sum import check_offer
from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import (
    CostNarrative,
    Kostenkategorie,
    OfferDocument,
    OfferHeader,
    OfferTotals,
    Position,
)
from invoice_controller.narrative import render_narrative_blocks
from invoice_controller.template.ods import write_kostenaufstellung
from tests.conftest import load_cases


def _table_text(table: Table) -> str:
    parts: list[str] = []
    for r in table.rows:
        for c in r.cells:
            t = (c.inner_text or "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _offer(
    narrative: CostNarrative | None,
    *,
    inv: str = "100000.00",
    neb: str = "0.00",
    filename: str = "2. Musterlieferant Angebot-Soll.pdf",
) -> OfferDocument:
    positions = [
        Position(pos="1", description="Maschine", line_total_net=Decimal(inv),
                 kategorie=Kostenkategorie.INVESTITIONSKOSTEN),
    ]
    if Decimal(neb) > 0:
        positions.append(
            Position(pos="2", description="Montage", line_total_net=Decimal(neb),
                     kategorie=Kostenkategorie.NEBENKOSTEN)
        )
    totals = OfferTotals(nettosumme=sum((Decimal(p.line_total_net) for p in positions), Decimal(0)))
    return OfferDocument(
        source_path=Path("/x") / filename,
        header=OfferHeader(vendor_name="Musterlieferant C", offer_number="0000", offer_date=date(2026, 3, 16)),
        positions=positions,
        totals=totals,
        cross_sum=check_offer(positions, totals),
        narrative=narrative,
    )


def test_render_blocks_amount_omitted_when_zero() -> None:
    nar = CostNarrative(
        investitionskosten_items="den Zweischneckenextruder",
        investitionskosten_seiten=[2, 3],
        nebenkosten_items="die Stahlbühne, das Engineering",
        nebenkosten_seiten=[3],
    )
    offer = _offer(nar, inv="4120000.00", neb="0.00")
    inv_text, neb_text = render_narrative_blocks(offer)

    assert inv_text == (
        "Die Investitionskosten 4.120.000,00 € beinhalten den Zweischneckenextruder "
        "(s. Anlage 2. Musterlieferant Angebot-Soll.pdf, Seite 2 und 3)"
    )
    # Nebenkosten subtotal is 0 -> no euro amount, but the block still renders.
    assert neb_text == (
        "Die Nebenkosten beinhalten die Stahlbühne, das Engineering "
        "(s. Anlage 2. Musterlieferant Angebot-Soll.pdf, Seite 3)"
    )


def test_render_blocks_shows_both_amounts_when_present() -> None:
    nar = CostNarrative(
        investitionskosten_items="die Maschine",
        investitionskosten_seiten=[1],
        nebenkosten_items="die Montage",
        nebenkosten_seiten=[1],
    )
    offer = _offer(nar, inv="100000.00", neb="21000.00")
    inv_text, neb_text = render_narrative_blocks(offer)
    assert "Die Investitionskosten 100.000,00 € beinhalten" in inv_text
    assert "Die Nebenkosten 21.000,00 € beinhalten" in neb_text


def test_render_blocks_skips_empty_item_lists() -> None:
    nar = CostNarrative(investitionskosten_items="die Maschine", investitionskosten_seiten=[1])
    inv_text, neb_text = render_narrative_blocks(_offer(nar))
    assert inv_text is not None
    assert neb_text is None


def test_render_blocks_none_narrative() -> None:
    assert render_narrative_blocks(_offer(None)) == (None, None)


def test_extract_offer_populates_narrative_with_stub(stub_agent_ek4_204, stub_summarize_agent, ek4_204_dir: Path) -> None:
    offer = extract_offer(
        ek4_204_dir / load_cases("ek4_204")[0]["pdf"],
        agent=stub_agent_ek4_204,
        summarize_agent=stub_summarize_agent,
    )
    assert offer.narrative is not None
    assert "Roh- und Fertigmaterialhandling" in offer.narrative.investitionskosten_items
    assert offer.narrative.nebenkosten_seiten == [3]


def test_narrative_written_to_separate_sheet(tmp_path: Path) -> None:
    nar = CostNarrative(
        investitionskosten_items="den Zweischneckenextruder",
        investitionskosten_seiten=[2, 3],
        nebenkosten_items="die Stahlbühne",
        nebenkosten_seiten=[3],
    )
    offer = _offer(nar, inv="4120000.00", neb="0.00")
    out = tmp_path / "out.ods"
    write_kostenaufstellung([offer], out)

    doc = Document(str(out))
    sheet_names = [t.name for t in doc.body.tables]
    assert sheet_names == ["Kostenaufstellung", "Kostenbeschreibung"]

    kosten_text = _table_text(doc.body.tables[0])
    beschr_text = _table_text(doc.body.tables[1])

    # The narrative lives on the new sheet, and the Kostenaufstellung sheet stays clean.
    assert "Die Investitionskosten 4.120.000,00 € beinhalten den Zweischneckenextruder" in beschr_text
    assert "Die Nebenkosten beinhalten die Stahlbühne" in beschr_text
    assert "Seite 2 und 3" in beschr_text
    assert "Die Investitionskosten" not in kosten_text
    # The SOLL header is repeated on the description sheet as each offer's header.
    assert "SOLL: Musterlieferant C" in beschr_text


def test_no_description_sheet_without_narrative(tmp_path: Path) -> None:
    offer = _offer(None)
    out = tmp_path / "out.ods"
    write_kostenaufstellung([offer], out)
    doc = Document(str(out))
    assert [t.name for t in doc.body.tables] == ["Kostenaufstellung"]
