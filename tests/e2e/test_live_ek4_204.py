"""Live E2E tests against the EK4_204 example corpus — hits the real LLM API.

Run with: `uv run pytest tests/e2e -m live --run-live`

These tests cost real money on each run (~$0.05 for the 3 EK4_204 offers at gemini-2.5-flash).
Use them after meaningful changes to the prompt or extraction pipeline. Daily CI should
NOT enable --run-live unless you've budgeted for it.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_controller.extract.offer import extract_offer

live = pytest.mark.live


@pytest.fixture
def has_llm_key() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


@live
def test_live_atb_extracts_two_positions(ek4_204_dir: Path, has_llm_key: bool) -> None:
    if not has_llm_key:
        pytest.skip("no LLM API key set")

    offer = extract_offer(ek4_204_dir / "6. atb Angebot-Soll- Netzanschluss.pdf")

    assert offer.header.vendor_name.lower().startswith("atb")
    assert offer.header.offer_number == "2400104"
    assert len(offer.positions) == 2, (
        f"expected 2 priced positions (Unterverteilung + Richtpreis Montage), "
        f"got {len(offer.positions)} — likely sub-item hallucination"
    )
    assert offer.cross_sum.passed, offer.cross_sum.message


@live
def test_live_munk_extracts_three_positions(ek4_204_dir: Path, has_llm_key: bool) -> None:
    if not has_llm_key:
        pytest.skip("no LLM API key set")

    offer = extract_offer(ek4_204_dir / "5. Munk Angebot-Soll-Gleichrichteranlage.pdf")

    assert "MUNK" in offer.header.vendor_name.upper()
    assert offer.header.offer_number == "88.612-100"
    assert len(offer.positions) == 3
    assert offer.cross_sum.passed
    assert offer.cross_sum.actual == Decimal("405260.00")


@live
def test_live_lr_extracts_sonderpreis_and_cross_sum(ek4_204_dir: Path, has_llm_key: bool) -> None:
    """L&R has 19 priced positions in the offer but bundles Pos 1-9 under a group price
    (211.500 € for Pos 1-9), so a faithful extraction may yield either 19 individual entries
    OR partially-bundled (e.g. one '1-9' entry + 10 individual entries). What MUST hold:
    cross-sum passes, and the Sonderpreis (negotiated final price) is captured separately."""
    if not has_llm_key:
        pytest.skip("no LLM API key set")

    offer = extract_offer(ek4_204_dir / "4. L&R Angebot-SOLL-K__lteanlage.pdf")

    assert offer.header.vendor_name.startswith("L&R")
    assert offer.header.offer_number == "AN230737-2.03"
    assert 1 <= len(offer.positions) <= 19
    assert offer.cross_sum.passed, offer.cross_sum.message
    assert offer.totals.nettosumme == Decimal("333400.00"), (
        "Nettosumme = Gesamtpreis Pos. 1-19 (the gross sum BEFORE the Sonderpreis discount)"
    )
    assert offer.totals.sonderpreis == Decimal("295000.00"), (
        "Sonderpreis must be captured separately from Nettosumme"
    )
