"""Unit regression tests for EK4_204 — pipeline correctness given known LLM outputs.

These tests do NOT hit a live LLM. They feed pre-recorded "ideal" LLM responses through
the stub provider and assert the pipeline produces the expected OfferDocument.

For real LLM-quality regression, see tests/e2e/test_live_ek4_204.py.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import Kostenkategorie, OfferDocument


def _extract_one(stub_agent, ek4_204_dir: Path, filename: str) -> OfferDocument:
    pdf = ek4_204_dir / filename
    assert pdf.exists(), f"missing fixture PDF: {pdf}"
    # These tests cover extraction only; the cost narrative is a separate LLM call exercised
    # in test_narrative.py, so disable it here to keep the unit suite offline.
    return extract_offer(pdf, agent=stub_agent, with_narrative=False)


def test_atb_extraction(stub_agent_ek4_204, ek4_204_dir: Path) -> None:
    offer = _extract_one(stub_agent_ek4_204, ek4_204_dir, "6. atb Angebot-Soll- Netzanschluss.pdf")

    assert offer.header.vendor_name == "atb Elektronische Steuerungen GmbH"
    assert offer.header.offer_number == "2400104"
    assert offer.header.offer_date.isoformat() == "2024-04-08"
    assert offer.header.title == "Netzanschluss"

    assert len(offer.positions) == 2
    p1, p2 = offer.positions
    assert p1.pos == "1"
    assert p1.line_total_net == Decimal("18759.80")
    assert p1.kategorie == Kostenkategorie.NEBENKOSTEN
    assert p2.pos == "2"
    assert p2.line_total_net == Decimal("89880.20")
    assert p2.kategorie == Kostenkategorie.INVESTITIONSKOSTEN

    assert offer.totals.nettosumme == Decimal("108640.00")
    assert offer.cross_sum.passed
    assert offer.cross_sum.actual == Decimal("108640.00")


def test_munk_extraction(stub_agent_ek4_204, ek4_204_dir: Path) -> None:
    offer = _extract_one(stub_agent_ek4_204, ek4_204_dir, "5. Munk Angebot-Soll-Gleichrichteranlage.pdf")

    assert offer.header.vendor_name == "MUNK GmbH"
    assert offer.header.offer_number == "88.612-100"
    assert len(offer.positions) == 3
    assert all(p.kategorie == Kostenkategorie.INVESTITIONSKOSTEN for p in offer.positions)
    assert offer.positions[0].line_total_net == Decimal("234600.00")
    assert sum((p.line_total_net for p in offer.positions), Decimal(0)) == Decimal("405260.00")
    assert offer.cross_sum.passed


def test_lr_extraction_with_sonderpreis(stub_agent_ek4_204, ek4_204_dir: Path) -> None:
    offer = _extract_one(stub_agent_ek4_204, ek4_204_dir, "4. L&R Angebot-SOLL-K__lteanlage.pdf")

    assert offer.header.vendor_name.startswith("L&R")
    assert offer.header.offer_number == "AN230737-2.03"
    assert len(offer.positions) == 19

    total = sum((p.line_total_net for p in offer.positions), Decimal(0))
    assert total == Decimal("332800.00"), f"Σ(positions)={total}"
    assert offer.totals.nettosumme == Decimal("332800.00")
    assert offer.totals.sonderpreis == Decimal("295000.00"), (
        "L&R has a negotiated Sonderpreis that must be captured separately"
    )
    assert offer.cross_sum.passed

    nebenkosten_positions = [p for p in offer.positions if p.kategorie == Kostenkategorie.NEBENKOSTEN]
    assert {p.pos for p in nebenkosten_positions} == {"10", "11"}, (
        "Pos 10 (Montage) and Pos 11 (Inbetriebnahme) are Nebenkosten per ground truth"
    )


def test_cross_sum_classification_split(stub_agent_ek4_204, ek4_204_dir: Path) -> None:
    """Investitionskosten + Nebenkosten + Nachlass = Σ(positions) per classification."""
    offer = _extract_one(stub_agent_ek4_204, ek4_204_dir, "4. L&R Angebot-SOLL-K__lteanlage.pdf")

    inv = sum((p.line_total_net for p in offer.positions if p.kategorie == Kostenkategorie.INVESTITIONSKOSTEN), Decimal(0))
    neb = sum((p.line_total_net for p in offer.positions if p.kategorie == Kostenkategorie.NEBENKOSTEN), Decimal(0))
    nac = sum((p.line_total_net for p in offer.positions if p.kategorie == Kostenkategorie.NACHLASS), Decimal(0))

    assert inv + neb + nac == offer.positions_sum
    assert neb == Decimal("21000.00"), "L&R ground truth: Σ Nebenkosten = 21.000,00 (Pos 10 + Pos 11)"


def test_stub_does_not_hit_network(stub_agent_ek4_204, ek4_204_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check: the stub agent never falls through to a real LLM client."""

    def fail(*_args, **_kwargs):
        raise RuntimeError("network not allowed in unit tests")

    monkeypatch.setattr("anthropic.Anthropic", fail, raising=False)
    monkeypatch.setattr("google.genai.Client", fail, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    offer = _extract_one(stub_agent_ek4_204, ek4_204_dir, "6. atb Angebot-Soll- Netzanschluss.pdf")
    assert offer.header.offer_number == "2400104"
