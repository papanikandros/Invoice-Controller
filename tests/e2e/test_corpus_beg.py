"""Live BEG regression against the ground-truth Kostenzusammenstellungen.

Run: `uv run pytest tests/e2e/test_corpus_beg.py --run-live` (LLM + vision calls —
the heaviest live suite in the project; ~30 calls per EM project).

Contract (PLAN.md §Phase 2b): mechanical columns must reconcile with the
consultant's sheet; judgment cells (förderfähig) and payment cells whose proofs are
missing from the corpus are asserted FLAGGED, not equal. Row shape may differ where
the consultant folded/unfolded Schlussrechnungen — per-vendor Σ Re-Betrag is the
folding-invariant comparison.

Validated live 2026-08-31: Aepker matches row-for-row to the cent (incl. the
ZUGFeRD-parsed Zacharias invoice); Madroch reconciles per-vendor with two known,
flagged divergences (SAT brutto missing on the scan → netto fallback; Zimmerei
cumulative netto read 156,93 € above the consultant's figure — both rows carry
red flags for review).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from invoice_controller.match.vendor import normalize_vendor

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "BEG"

pytestmark = pytest.mark.live

PROJECTS = {
    "EM_Aepker": ("EM_Aepker/EM", "Kostenzusammenstellung_EM_Aepker.xlsx"),
    "EM_Madroch": ("EM_Madroch/EM Dämm. AW + Gauben", "Kostenzusammenstellung_EM_Madroch.xlsx"),
}


def _ground_truth_rows(path: Path) -> list[tuple[str, str, Decimal]]:
    ws = load_workbook(path, data_only=True)["Kostenzusammenstellung"]
    rows = []
    for row in ws.iter_rows(min_row=3):
        firma, renr, betrag = row[1].value, row[2].value, row[5].value
        if firma and renr and isinstance(betrag, (int, float)):
            rows.append((str(firma).strip(), str(renr).strip(), Decimal(str(round(betrag, 2)))))
    return rows


def _by_vendor(rows: list[tuple[str, str, Decimal]]) -> dict[frozenset[str], Decimal]:
    sums: dict[frozenset[str], Decimal] = defaultdict(lambda: Decimal(0))
    for firma, _renr, betrag in rows:
        sums[frozenset(normalize_vendor(firma))] += betrag
    return dict(sums)


@pytest.fixture(scope="module")
def beg_results():
    from invoice_controller.extract.beg import build_kostenzusammenstellung

    results = {}
    for pid, (rel, _gt) in PROJECTS.items():
        project = EXAMPLES / rel
        if not project.exists():
            continue
        results[pid] = build_kostenzusammenstellung(
            project, output_path=Path("tmp") / f"e2e_beg_{pid}.xlsx"
        )
    if not results:
        pytest.skip("BEG corpus not present")
    return results


@pytest.mark.parametrize("pid", list(PROJECTS))
def test_per_vendor_sums_reconcile_or_rows_are_flagged(pid, beg_results):
    if pid not in beg_results:
        pytest.skip(f"{pid} corpus absent")
    result = beg_results[pid]
    gt_rows = _ground_truth_rows(EXAMPLES / PROJECTS[pid][0] / PROJECTS[pid][1])
    gt_sums = _by_vendor(gt_rows)

    gen_rows = [(r.firma, r.re_nr, r.re_betrag or Decimal(0)) for r in result.table.rows]
    gen_sums = _by_vendor(gen_rows)
    flags_by_vendor: dict[frozenset[str], bool] = defaultdict(bool)
    for r in result.table.rows:
        key = frozenset(normalize_vendor(r.firma))
        flags_by_vendor[key] = flags_by_vendor[key] or bool(r.flags)

    for vendor_key, gt_sum in gt_sums.items():
        gen = next(
            (s for k, s in gen_sums.items() if k & vendor_key), None
        )
        assert gen is not None, f"{pid}: vendor {set(vendor_key)} missing from generated table"
        flagged = any(f for k, f in flags_by_vendor.items() if k & vendor_key)
        assert abs(gen - gt_sum) <= Decimal("0.02") or flagged, (
            f"{pid}: vendor {set(vendor_key)} Σ {gen} ≠ ground truth {gt_sum} and no review flag"
        )


def test_aepker_rows_match_exactly(beg_results):
    """The stronger contract for the project where extraction is fully clean
    (validated 2026-08-31): identical (Re-Nr., Re-Betrag) sets."""
    if "EM_Aepker" not in beg_results:
        pytest.skip("Aepker corpus absent")
    result = beg_results["EM_Aepker"]
    gt = {(renr, betrag) for _f, renr, betrag in _ground_truth_rows(
        EXAMPLES / PROJECTS["EM_Aepker"][0] / PROJECTS["EM_Aepker"][1]
    )}
    gen = {(r.re_nr, r.re_betrag) for r in result.table.rows}
    assert gen == gt
