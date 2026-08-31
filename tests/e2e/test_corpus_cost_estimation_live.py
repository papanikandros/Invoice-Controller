"""Live cost-estimation regression against the 10-project close-out corpus.

For each project we run the real cost-estimation pipeline on the vendor offer PDFs and check
the extraction against the consultant's hand-built Kostenaufstellung (recovered
by tests/corpus/kostenaufstellung.py):

  * every offer's cross-sum holds (statements excepted — they have none), and
  * each offer's extracted net total reconciles to one of the ground-truth SOLL
    block totals.

The IK/NK split ratio is compared loosely and reported, not hard-asserted: the
consultant overrides cost-estimation's LLM classification during review, so the ratio is a
proposal, whereas the net total is the faithful-extraction contract.

Run: `uv run pytest tests/e2e/test_corpus_cost_estimation_live.py --run-live`  (costs API money)
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from invoice_controller.extract.offer import extract_offer
from invoice_controller.models import Kostenkategorie
from tests.corpus import PROJECT_IDS, load_project
from tests.corpus.kostenaufstellung import parse_kostenaufstellung

live = pytest.mark.live

TOL = Decimal("2.00")  # € tolerance when reconciling to a ground-truth block total


def _has_key() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def _ik_nk(offer) -> tuple[Decimal, Decimal]:
    ik = sum(
        (p.line_total_net for p in offer.positions
         if p.line_total_net is not None and p.kategorie == Kostenkategorie.INVESTITIONSKOSTEN),
        Decimal(0),
    )
    nk = sum(
        (p.line_total_net for p in offer.positions
         if p.line_total_net is not None and p.kategorie == Kostenkategorie.NEBENKOSTEN),
        Decimal(0),
    )
    return ik, nk


@live
@pytest.mark.parametrize("pid", PROJECT_IDS)
def test_cost_estimation_reconciles_to_kostenaufstellung(pid):
    if not _has_key():
        pytest.skip("no LLM API key set")

    proj = load_project(pid)
    ground = [b for b in parse_kostenaufstellung(proj.kostenaufstellung_pdf).blocks if b.gesamt is not None]
    assert ground, f"{pid}: no ground-truth block totals"
    block_totals = [b.gesamt for b in ground]

    unreconciled = []
    for offer_pdf in proj.offers:
        offer = extract_offer(offer_pdf, with_narrative=False)
        net = offer.totals.nettosumme or offer.total_mandatory

        # Offers must cross-sum; statements legitimately have no internal check.
        if not offer.cross_sum.not_applicable:
            assert offer.cross_sum.passed, (
                f"{pid}/{offer_pdf.name}: cross-sum failed: {offer.cross_sum.message}"
            )

        if not any(abs(net - g) <= TOL for g in block_totals):
            unreconciled.append((offer_pdf.name, net))

    assert not unreconciled, (
        f"{pid}: offer nets not matching any Kostenaufstellung block "
        f"{[float(g) for g in block_totals]}: {unreconciled}"
    )
