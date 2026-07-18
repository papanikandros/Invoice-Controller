"""Live E2E test for the scanned client-statement path — hits the real multimodal LLM API.

Run with: `uv run pytest tests/e2e -m live --run-live`

This hits the real (multimodal) LLM API because the source PDF is an image-only scan with no
text layer — it exercises the full vision-LLM fallback. Costs a few cents per run.

The PDF filename and expected figures come from the gitignored recorded fixture, so no real
client/vendor identifiers appear in committed source.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import DocumentKind
from tests.conftest import EXAMPLES_DIR, load_cases, load_fixture

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
def test_live_statement_via_vision(has_llm_key: bool) -> None:
    if not has_llm_key:
        pytest.skip("no LLM API key set")

    case = load_cases("ek4_322")[0]
    fx = load_fixture("ek4_322", case["stem"])
    pdf = EXAMPLES_DIR / "EK4_322" / case["pdf"]

    offer = extract_offer(pdf, with_narrative=False)

    # Scanned PDF with no text layer → no local OCR engine here → cloud vision fallback.
    assert offer.extraction_method == "vision-llm"
    assert offer.kind is DocumentKind.STATEMENT
    assert offer.vat_basis_unstated
    assert offer.cross_sum.not_applicable
    assert not offer.cross_sum.passed

    # Same cost lines as recorded; operating metrics (t/a, kWh/a) must NOT be captured.
    expected_sum = sum((Decimal(p["line_total_net"]) for p in fx["positions"]), Decimal(0))
    assert len(offer.positions) == len(fx["positions"])
    assert offer.positions_sum == expected_sum
