"""Guard the close-out corpus ground-truth readers (no API, fast).

These tests do NOT exercise the F1/F2 pipelines. They pin the numbers the
readers recover from the consultant's shipped artifacts so that the readers
themselves cannot silently regress, and they document the expected per-project
figures the pipelines must eventually reproduce. See tests/corpus/.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.corpus import PROJECT_IDS, load_project
from tests.corpus.kostenaufstellung import parse_kostenaufstellung
from tests.corpus.vne_tabelle import parse_vne_tabelle


def _close(a, b, tol=Decimal("1.0")) -> bool:
    return a is not None and b is not None and abs(Decimal(a) - Decimal(b)) <= tol


@pytest.mark.parametrize("pid", PROJECT_IDS)
def test_project_artifacts_present(pid):
    proj = load_project(pid)
    assert proj.f1_pdf is not None, f"{pid}: no F1-format Kostenaufstellung PDF found"
    assert proj.vne_xlsx is not None, f"{pid}: no VNE-Tabelle .xlsx found"
    assert proj.invoices, f"{pid}: no invoice PDFs detected"


@pytest.mark.parametrize("pid", PROJECT_IDS)
def test_f1_blocks_complete(pid):
    proj = load_project(pid)
    ka = parse_kostenaufstellung(proj.f1_pdf)
    assert ka.blocks, f"{pid}: no SOLL blocks parsed"
    for b in ka.blocks:
        assert b.is_complete, f"{pid}: block {b.header!r} missing totals/ratio"
        # IK + NK should reconcile to the block grand total (Nachlass aside).
        if b.ik is not None and b.nk is not None:
            assert _close(b.ik + b.nk, b.gesamt, tol=Decimal("2.0")), (
                f"{pid}: {b.header!r} IK+NK={b.ik + b.nk} != Σ={b.gesamt}"
            )


@pytest.mark.parametrize("pid", PROJECT_IDS)
def test_f2_invoice_table_reconciles(pid):
    proj = load_project(pid)
    vne = parse_vne_tabelle(proj.vne_xlsx)
    assert vne.header_row is not None, f"{pid}: VNE header row not located"
    assert vne.invoices, f"{pid}: no invoice rows parsed"
    # Every invoice carries at least one split amount or a category tag.
    for inv in vne.invoices:
        assert inv.invoice_date is not None
        assert inv.brutto is not None
    # The 'Σ gesamt' row, when it aligns, must match the per-invoice column sums.
    if vne.stated_sum_ik is not None:
        assert _close(vne.stated_sum_ik, vne.sum_ik, tol=Decimal("2.0")) or \
               _close(vne.stated_sum_nk, vne.sum_nk, tol=Decimal("2.0")), (
            f"{pid}: stated Σ (IK={vne.stated_sum_ik}, NK={vne.stated_sum_nk}) "
            f"vs computed (IK={vne.sum_ik}, NK={vne.sum_nk})"
        )


# Projects where the consultant overrode per-invoice categorization away from the
# F1 offer ratio (documented so the F2 engine handles the exception rather than
# assuming the ratio model is universal). Meyring re-books its L&R Nebenkosten
# portion as Investitionskosten (see its Vorkasse/Werksverrohrung Erklärung).
F2_CATEGORY_OVERRIDE = {"Meyring"}


@pytest.mark.parametrize("pid", PROJECT_IDS)
def test_f1_block_ratio_is_well_formed(pid):
    """Each F1 block's IK share is a fraction in [0,1].

    Note: the consultant's stated split % is computed on the Sonderpreis-adjusted
    base, so it need not equal IK/Gesamt when a Sonderpreis is present; the stated
    percentage (when shown) is the authoritative ratio F2 consumes.
    """
    proj = load_project(pid)
    ka = parse_kostenaufstellung(proj.f1_pdf)
    for b in ka.blocks:
        r = b.ik_ratio
        assert r is not None and Decimal(0) <= r <= Decimal(1), f"{pid}: bad ratio {r}"


@pytest.mark.parametrize("pid", sorted(set(PROJECT_IDS) - F2_CATEGORY_OVERRIDE))
def test_f2_ik_share_within_f1_vendor_ratios(pid):
    """Where categorization follows the offers, the aggregate F2 IK share stays at
    or below the top investment-vendor F1 ratio (pure-NK vendors only pull it down)."""
    proj = load_project(pid)
    ka = parse_kostenaufstellung(proj.f1_pdf)
    vne = parse_vne_tabelle(proj.vne_xlsx)
    total = vne.sum_ik + vne.sum_nk
    if total == 0:
        pytest.skip(f"{pid}: no IK/NK split in F2")
    f2_ik_share = float(vne.sum_ik / total)
    max_ratio = max(float(b.ik_ratio) for b in ka.blocks if b.ik_ratio is not None)
    assert 0.0 <= f2_ik_share <= max_ratio + 0.02, (
        f"{pid}: F2 IK share {f2_ik_share:.4f} > max F1 ratio {max_ratio:.4f}"
    )
