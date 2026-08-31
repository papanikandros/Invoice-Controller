"""vne-generation regression scaffold against the close-out corpus VNE-Tabelle ground truth.

These cases pin the contract
the vne-generation engine must satisfy, taken from tests/corpus/vne_tabelle.py:

  * the number of invoice rows recovered,
  * the Σ Investitionskosten and Σ Nebenkosten (the confirmed vne-generation test contract),
    from the per-invoice split columns.

Per the design decisions: vne-generation assigns each invoice to a vendor and splits its net
IK/NK by that vendor's cost-estimation offer ratio (per-vendor, not per-position); the
Bescheid/ESK figures (Förderbetrag, Bewilligungszeitraum, Kostendeckel) come from
projekt.yaml, not from the offers/invoices.

When vne-generation lands, expose `build_vne_tabelle(project_dir, config) -> VneResult` and
flip `VNE_IMPLEMENTED` to True.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.corpus import CATEGORY_OVERRIDE, PROJECT_IDS, load_project
from tests.corpus.vne_tabelle import parse_vne_tabelle

VNE_IMPLEMENTED = False

# Projects that re-book a vendor's Nebenkosten portion as Investitionskosten via a project
# note; vne-generation needs a projekt.yaml categorization override to reproduce them.
NEEDS_CATEGORY_OVERRIDE = CATEGORY_OVERRIDE


def _expected(pid):
    vne = parse_vne_tabelle(load_project(pid).vne_xlsx)
    return {
        "n_invoices": len(vne.invoices),
        "sum_ik": vne.sum_ik,
        "sum_nk": vne.sum_nk,
        "sum_ek": vne.sum_ek,
    }


@pytest.mark.parametrize("pid", PROJECT_IDS)
def test_vne_generation_reproduces_vne_tabelle(pid):
    if not VNE_IMPLEMENTED:
        pytest.skip("vne-generation not implemented yet — scaffold pins the target contract")

    from invoice_controller.config import load_project_config  # noqa: F401
    from invoice_controller.extract.vne import build_vne_tabelle  # noqa: F401

    exp = _expected(pid)
    proj = load_project(pid)
    config = load_project_config(proj.dir / "projekt.yaml")
    result = build_vne_tabelle(proj.dir, config)

    assert len(result.invoices) == exp["n_invoices"], (
        f"{pid}: got {len(result.invoices)} invoice rows, expected {exp['n_invoices']}"
    )
    assert abs(result.sum_ik - exp["sum_ik"]) <= Decimal("1.0"), (
        f"{pid}: Σ IK {result.sum_ik} != {exp['sum_ik']}"
    )
    assert abs(result.sum_nk - exp["sum_nk"]) <= Decimal("1.0"), (
        f"{pid}: Σ NK {result.sum_nk} != {exp['sum_nk']}"
    )
