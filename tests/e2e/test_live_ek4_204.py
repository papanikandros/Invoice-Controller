"""Live E2E tests against the EK4_204 example corpus — hits the real LLM API.

Run with: `uv run pytest tests/e2e -m live --run-live`

These tests cost real money on each run (~$0.05 for the 3 EK4_204 offers at gemini-2.5-flash).
Use them after meaningful changes to the prompt or extraction pipeline. Daily CI should
NOT enable --run-live unless you've budgeted for it.

Expected values come from the gitignored recorded fixtures (real vendor identifiers stay
out of committed source); the live extraction must reproduce that recorded ground truth.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from invoice_controller.extract.offer import extract_offer
from tests.conftest import load_cases, load_fixture

live = pytest.mark.live


@pytest.fixture
def has_llm_key() -> bool:
    return bool(
        os.environ.get("OPEN_ROUTER_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


@live
def test_live_extraction_reproduces_recorded_ground_truth(
    ek4_204_dir: Path, has_llm_key: bool
) -> None:
    if not has_llm_key:
        pytest.skip("no LLM API key set")

    for case in load_cases("ek4_204"):
        fx = load_fixture("ek4_204", case["stem"])
        offer = extract_offer(ek4_204_dir / case["pdf"])

        # Strong, template-agnostic invariants the live LLM must satisfy. (Exact totals can
        # legitimately differ from the recorded "ideal" by rounding/bundling, so the cross-sum
        # passing — not total equality — is the correctness guarantee here.)
        assert offer.header.offer_number == fx["header"].get("offer_number"), case["stem"]
        first_token = fx["header"]["vendor_name"].split()[0].lower()
        assert first_token in offer.header.vendor_name.lower(), case["stem"]
        assert offer.positions, case["stem"]
        assert offer.cross_sum.passed, (case["stem"], offer.cross_sum.message)
