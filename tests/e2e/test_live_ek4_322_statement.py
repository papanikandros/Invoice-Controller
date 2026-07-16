"""Live E2E test for the scanned client-statement path (EK4_322 Craemer Stellungnahme).

Run with: `uv run pytest tests/e2e -m live --run-live`

This hits the real (multimodal) LLM API because the source PDF is an image-only scan with no
text layer — it exercises the full vision-LLM fallback. Costs a few cents per run.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import DocumentKind, Kostenkategorie

live = pytest.mark.live

STATEMENT_PDF = Path("examples/EK4_322/7. Craemer Stellungnahme.pdf")


@pytest.fixture
def has_llm_key() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


@live
def test_live_craemer_statement_via_vision(has_llm_key: bool) -> None:
    if not has_llm_key:
        pytest.skip("no LLM API key set")

    offer = extract_offer(STATEMENT_PDF, with_narrative=False)

    # Scanned PDF with no text layer → no local OCR engine here → cloud vision fallback.
    assert offer.extraction_method == "vision-llm"
    assert offer.kind is DocumentKind.STATEMENT
    assert offer.vat_basis_unstated
    assert offer.cross_sum.not_applicable
    assert not offer.cross_sum.passed

    # Six cost lines; the t/a and kWh/a operating metrics must NOT be captured as positions.
    assert len(offer.positions) == 6
    assert offer.positions_sum == Decimal("289950.00")

    # The line explicitly tagged "(Nebenkosten)" is classified as such.
    farbmess = next(p for p in offer.positions if "Farbmess" in p.description)
    assert farbmess.kategorie is Kostenkategorie.NEBENKOSTEN
    assert farbmess.line_total_net == Decimal("2950.00")
