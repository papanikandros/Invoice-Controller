from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic_ai import Agent

from invoice_controller.extract.cross_sum import check_statement
from invoice_controller.extract.offer import extract_offer, kind_from_filename
from invoice_controller.llm.extract import ExtractedOffer
from invoice_controller.models import (
    DocumentKind,
    Kostenkategorie,
    OfferDocument,
    OfferHeader,
    OfferTotals,
    Position,
)
from invoice_controller.template.ods import _format_soll_header, write_kostenaufstellung
from tests.conftest import load_fixture, make_fingerprint_agent
from tests.ods_inspect import read_kostenaufstellung


def _statement_header() -> OfferHeader:
    return OfferHeader(
        vendor_name="Musterfirma GmbH",
        vendor_short="Musterfirma",
        offer_number="Stellungnahme",
        offer_date=date(2026, 6, 23),
    )

# A single page of synthetic statement text carrying the stub agent's fingerprint. The real
# statement PDF is a scan (no text layer), so the unit path injects text instead of reading it.
_STATEMENT_PAGE = (
    "Bestätigung Materialverbrauch und Menge Mahlgutausschuss 2024\n"
    "Aktueller Einsatz von HDPE-Recyclat: 11.256 t/a\n"
    "Jährlicher Energieverbrauch der Anlagen: 16.665.032 kWh/a\n"
    "Darüber hinaus fallen Kosten an, für die noch kein Angebot vorliegt:\n"
    "Kälteanlage Werksverrohrung: 20.000 Euro\n"
)


def _statement_pages(path: Path) -> list[str]:
    return [_STATEMENT_PAGE]


def test_check_statement_is_not_applicable() -> None:
    positions = [
        Position(pos="1", description="A", line_total_net=Decimal("20000.00")),
        Position(pos="2", description="B", line_total_net=Decimal("2950.00")),
    ]
    result = check_statement(positions)
    assert result.not_applicable
    assert not result.passed  # nothing was reconciled
    assert result.expected is None
    assert result.actual == Decimal("22950.00")
    assert "kein Dokument-Gesamtbetrag" in (result.message or "")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("7. Musterfirma Stellungnahme.pdf", DocumentKind.STATEMENT),
        ("Kostenschätzung_2026.pdf", DocumentKind.STATEMENT),
        ("Eigenerklärung Musterfirma.pdf", DocumentKind.STATEMENT),
        ("2. Musterlieferant Angebot-Soll.pdf", None),
        ("Angebot1253187.pdf", None),
    ],
)
def test_kind_from_filename(name: str, expected: DocumentKind | None) -> None:
    assert kind_from_filename(Path(name)) == expected


def _run_pipeline(monkeypatch: pytest.MonkeyPatch, path: Path, agent: Agent[None, ExtractedOffer]) -> OfferDocument:
    monkeypatch.setattr("invoice_controller.extract.offer.extract_pages", _statement_pages)
    return extract_offer(path, agent=agent, with_narrative=False)


def test_statement_pipeline_via_filename(
    monkeypatch: pytest.MonkeyPatch,
    stub_agent_ek4_322_statement: Agent[None, ExtractedOffer],
) -> None:
    path = Path("x/7. Musterfirma Stellungnahme.pdf")
    offer = _run_pipeline(monkeypatch, path, stub_agent_ek4_322_statement)

    assert offer.kind is DocumentKind.STATEMENT
    assert offer.vat_basis_unstated
    assert offer.cross_sum.not_applicable
    assert not offer.cross_sum.passed
    # Σ of all six listed lines; the operating metrics (t/a, kWh/a) are not positions.
    assert offer.positions_sum == Decimal("289950.00")
    assert len(offer.positions) == 6
    assert offer.positions[-1].kategorie is Kostenkategorie.NEBENKOSTEN


def test_filename_overrides_llm_offer_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if the LLM thinks it's an offer (doc_type default), a statement filename wins.
    payload = load_fixture("ek4_322", "craemer_stellungnahme")
    payload = {**payload, "doc_type": "offer"}
    agent = make_fingerprint_agent({"Werksverrohrung": payload})
    offer = _run_pipeline(monkeypatch, Path("x/7. Musterfirma Stellungnahme.pdf"), agent)
    assert offer.kind is DocumentKind.STATEMENT


def test_llm_classification_used_when_filename_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Filename gives no signal → the LLM's doc_type=statement is the fallback.
    agent = make_fingerprint_agent(
        {"Werksverrohrung": load_fixture("ek4_322", "craemer_stellungnahme")}
    )
    offer = _run_pipeline(monkeypatch, Path("x/dokument_42.pdf"), agent)
    assert offer.kind is DocumentKind.STATEMENT
    assert offer.cross_sum.not_applicable


def test_statement_soll_header_is_labeled() -> None:
    pos = [Position(pos="1", description="A", line_total_net=Decimal("1.0"))]
    offer = OfferDocument(
        source_path=Path("x.pdf"),
        header=_statement_header(),
        positions=pos,
        totals=OfferTotals(),
        cross_sum=check_statement(pos),
        kind=DocumentKind.STATEMENT,
    )
    header = _format_soll_header(offer)
    assert header.startswith("SOLL: SCHÄTZUNG lt. Stellungnahme")
    assert "kein Angebot" in header
    assert "netto angenommen" in header


def test_statement_renders_into_kostenaufstellung(
    monkeypatch: pytest.MonkeyPatch,
    stub_agent_ek4_322_statement: Agent[None, ExtractedOffer],
    tmp_path: Path,
) -> None:
    offer = _run_pipeline(
        monkeypatch, Path("x/7. Musterfirma Stellungnahme.pdf"), stub_agent_ek4_322_statement
    )
    out = tmp_path / "stmt.ods"
    write_kostenaufstellung([offer], out)

    blocks = read_kostenaufstellung(out)
    assert len(blocks) == 1
    assert "SCHÄTZUNG" in blocks[0].soll_header
    assert len(blocks[0].rows_positions) == 6
    assert blocks[0].row_sum is not None
    # Gesamtkosten column of the Σ row equals Σ(listed lines).
    assert blocks[0].row_sum.value_at(2) == Decimal("289950.0")
