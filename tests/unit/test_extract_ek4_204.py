"""Unit regression tests for EK4_204 — pipeline correctness given known LLM outputs.

These tests do NOT hit a live LLM. They feed pre-recorded "ideal" LLM responses through
the stub provider and assert the pipeline faithfully surfaces them and cross-sums.

The recorded offers carry real vendor/client identifiers, so the fixtures and their case
manifest are gitignored; expected values are read from the fixtures rather than hardcoded,
and the whole module skips on a fresh clone. For real LLM-quality regression, see
tests/e2e/test_live_ek4_204.py.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import Kostenkategorie, OfferDocument
from tests.conftest import load_cases, load_fixture


def _extract(stub_agent, ek4_204_dir: Path, case: dict) -> OfferDocument:
    pdf = ek4_204_dir / case["pdf"]
    assert pdf.exists(), f"missing fixture PDF: {pdf}"
    # These tests cover extraction only; the cost narrative is a separate LLM call exercised
    # in test_narrative.py, so disable it here to keep the unit suite offline.
    return extract_offer(pdf, agent=stub_agent, with_narrative=False)


def test_extraction_roundtrips_each_recorded_offer(stub_agent_ek4_204, ek4_204_dir: Path) -> None:
    for case in load_cases("ek4_204"):
        fx = load_fixture("ek4_204", case["stem"])
        offer = _extract(stub_agent_ek4_204, ek4_204_dir, case)

        # Header, positions and totals must surface exactly as recorded.
        assert offer.header.vendor_name == fx["header"]["vendor_name"], case["stem"]
        assert offer.header.offer_number == fx["header"].get("offer_number"), case["stem"]
        assert len(offer.positions) == len(fx["positions"]), case["stem"]
        for pos, exp in zip(offer.positions, fx["positions"], strict=True):
            assert pos.pos == exp["pos"], case["stem"]
            if exp.get("line_total_net") is not None:
                assert pos.line_total_net == Decimal(exp["line_total_net"]), (case["stem"], pos.pos)
            assert pos.kategorie.value == exp["kategorie"], (case["stem"], pos.pos)

        if fx["totals"].get("nettosumme") is not None:
            assert offer.totals.nettosumme == Decimal(fx["totals"]["nettosumme"]), case["stem"]
        if fx["totals"].get("sonderpreis") is not None:
            assert offer.totals.sonderpreis == Decimal(fx["totals"]["sonderpreis"]), case["stem"]
        assert offer.cross_sum.passed, case["stem"]


def test_category_split_reconciles_to_positions_sum(stub_agent_ek4_204, ek4_204_dir: Path) -> None:
    """Investitionskosten + Nebenkosten + Nachlass = Σ(positions) per classification."""
    for case in load_cases("ek4_204"):
        offer = _extract(stub_agent_ek4_204, ek4_204_dir, case)
        by_cat = {
            k: sum((p.line_total_net for p in offer.positions if p.kategorie == k), Decimal(0))
            for k in (
                Kostenkategorie.INVESTITIONSKOSTEN,
                Kostenkategorie.NEBENKOSTEN,
                Kostenkategorie.NACHLASS,
            )
        }
        assert sum(by_cat.values()) == offer.positions_sum, case["stem"]


def test_stub_does_not_hit_network(
    stub_agent_ek4_204, ek4_204_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check: the stub agent never falls through to a real LLM client."""
    cases = load_cases("ek4_204")

    def fail(*_args, **_kwargs):
        raise RuntimeError("network not allowed in unit tests")

    monkeypatch.setattr("anthropic.Anthropic", fail, raising=False)
    monkeypatch.setattr("google.genai.Client", fail, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    offer = _extract(stub_agent_ek4_204, ek4_204_dir, cases[0])
    assert offer.header.vendor_name  # surfaced from the stub, no network
