from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import (
    DocumentKind,
    OfferHeader,
    OfferTotals,
    Position,
)
from invoice_controller.pdf.ocr import is_text_layer_empty


def test_is_text_layer_empty() -> None:
    assert is_text_layer_empty([])
    assert is_text_layer_empty(["", "   \n  "])
    assert is_text_layer_empty(["too short"])  # < 20 non-whitespace chars
    assert not is_text_layer_empty(["x" * 30])


def _fake_extraction() -> tuple[OfferHeader, list[Position], OfferTotals, DocumentKind]:
    header = OfferHeader(
        vendor_name="Craemer GmbH",
        offer_number="Stellungnahme",
        offer_date=date(2026, 6, 23),
    )
    positions = [
        Position(pos="1", description="Kälteanlage", line_total_net=Decimal("20000.00")),
        Position(pos="2", description="Farbmessgerät", line_total_net=Decimal("2950.00")),
    ]
    return header, positions, OfferTotals(), DocumentKind.STATEMENT


def test_scan_with_no_local_ocr_uses_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("invoice_controller.extract.offer.extract_pages", lambda p: [""])
    monkeypatch.setattr("invoice_controller.extract.offer.local_ocr_text", lambda p: None)
    monkeypatch.setattr(
        "invoice_controller.extract.offer.render_page_pngs", lambda p: [b"\x89PNG-fake"]
    )
    called: dict[str, bool] = {"vision": False, "text": False}

    def fake_vision(images, source_path, agent=None):  # type: ignore[no-untyped-def]
        called["vision"] = True
        return _fake_extraction()

    def fake_text(pages, source_path, agent=None):  # type: ignore[no-untyped-def]
        called["text"] = True
        return _fake_extraction()

    monkeypatch.setattr("invoice_controller.extract.offer.extract_offer_llm_vision", fake_vision)
    monkeypatch.setattr("invoice_controller.extract.offer.extract_offer_llm", fake_text)

    offer = extract_offer(Path("x/scan.pdf"), with_narrative=False)
    assert called["vision"] and not called["text"]
    assert offer.extraction_method == "vision-llm"
    assert offer.positions_sum == Decimal("22950.00")
    assert offer.narrative is None  # vision path has no page text to summarize


def test_scan_with_local_ocr_uses_text_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("invoice_controller.extract.offer.extract_pages", lambda p: [""])
    # Local OCR recovers a usable text layer → text LLM path, no cloud vision call.
    monkeypatch.setattr(
        "invoice_controller.extract.offer.local_ocr_text",
        lambda p: ["Kälteanlage Werksverrohrung: 20.000 Euro " * 3],
    )
    called: dict[str, bool] = {"vision": False, "text": False}

    def fake_vision(images, source_path, agent=None):  # type: ignore[no-untyped-def]
        called["vision"] = True
        return _fake_extraction()

    def fake_text(pages, source_path, agent=None):  # type: ignore[no-untyped-def]
        called["text"] = True
        return _fake_extraction()

    monkeypatch.setattr("invoice_controller.extract.offer.extract_offer_llm_vision", fake_vision)
    monkeypatch.setattr("invoice_controller.extract.offer.extract_offer_llm", fake_text)

    offer = extract_offer(Path("x/scan.pdf"), with_narrative=False)
    assert called["text"] and not called["vision"]
    assert offer.extraction_method == "tesseract+llm"
