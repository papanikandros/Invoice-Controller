"""R8 — corrections capture: semantic diff of generated vs consultant-corrected
Kostenaufstellung. Files are produced by the real writer, then edited the way a
consultant would (values changed in place, a row deleted, one added)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl

from invoice_controller.audit.corrections import (
    collect_corrections,
    read_blocks,
    write_corrections_jsonl,
)
from invoice_controller.models import (
    CrossSumCheck,
    DocumentKind,
    Kostenkategorie,
    OfferDocument,
    OfferHeader,
    OfferTotals,
    Position,
)
from invoice_controller.template.xlsx import write_kostenaufstellung


def _offer() -> OfferDocument:
    positions = [
        Position(pos="1", description="Wärmepumpe WP-12", line_total_net=Decimal("11200.00"),
                 kategorie=Kostenkategorie.INVESTITIONSKOSTEN),
        Position(pos="2", description="Montage", line_total_net=Decimal("1360.00"),
                 kategorie=Kostenkategorie.NEBENKOSTEN),
        Position(pos="3", description="Inbetriebnahme", line_total_net=Decimal("500.00"),
                 kategorie=Kostenkategorie.NEBENKOSTEN),
    ]
    total = Decimal("13060.00")
    return OfferDocument(
        source_path=Path("angebot.pdf"),
        header=OfferHeader(vendor_name="Muster Haustechnik GmbH", offer_number="2026-1",
                           offer_date=date(2026, 3, 1)),
        positions=positions,
        totals=OfferTotals(nettosumme=total),
        cross_sum=CrossSumCheck(expected=total, actual=total, tolerance=Decimal("0.02"), passed=True),
        kind=DocumentKind.OFFER,
    )


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    original = tmp_path / "generated.xlsx"
    write_kostenaufstellung([_offer()], original)
    corrected = tmp_path / "corrected.xlsx"
    wb = openpyxl.load_workbook(original)
    ws = wb.worksheets[0]
    for row in ws.iter_rows(min_col=1, max_col=5):
        first = str(row[0].value or "")
        if first == "1":                       # consultant fixes an amount
            row[2].value = 11500.00
            row[3].value = 11500.00
        if first == "3":                       # consultant renames a description
            row[1].value = "Inbetriebnahme und Einweisung"
    wb.save(corrected)
    return original, corrected


class TestCorrections:
    def test_reader_recovers_blocks_and_rows(self, tmp_path: Path) -> None:
        original, _ = _write_pair(tmp_path)
        blocks = read_blocks(original)
        [(header, rows)] = blocks.items()
        assert header.startswith("SOLL")
        assert [r.pos for r in rows] == ["1", "2", "3"]
        assert rows[0].gesamt == Decimal("11200.00")

    def test_amount_and_description_corrections(self, tmp_path: Path) -> None:
        original, corrected = _write_pair(tmp_path)
        corrections = collect_corrections(original, corrected)
        by = {(c.pos, c.field) for c in corrections if c.scope == "position-field"}
        assert ("1", "gesamt") in by and ("1", "investitionskosten") in by
        assert ("3", "description") in by
        amount = next(c for c in corrections if c.pos == "1" and c.field == "gesamt")
        assert amount.original == "11200.00" and amount.corrected == "11500.00"

    def test_identical_files_yield_nothing(self, tmp_path: Path) -> None:
        original, _ = _write_pair(tmp_path)
        assert collect_corrections(original, original) == []

    def test_jsonl_output(self, tmp_path: Path) -> None:
        original, corrected = _write_pair(tmp_path)
        out = write_corrections_jsonl(collect_corrections(original, corrected), tmp_path / "k.jsonl")
        lines = [json.loads(l) for l in out.read_text().splitlines()]
        assert all(l["scope"] == "position-field" for l in lines)
        assert any(l["field"] == "description" for l in lines)
