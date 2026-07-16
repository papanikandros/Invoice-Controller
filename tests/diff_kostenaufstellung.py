"""Semantic diff between two Kostenaufstellung .ods files (ground truth vs. tool output).

Useful for:
- Measuring how close the LLM extraction comes to the consultant's hand-built .ods
- Catching regressions after a prompt change
- Quick visual inspection of what's different

Run: `uv run python -m tests.diff_kostenaufstellung <ground_truth.ods> <tool_output.ods>`
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from tests.ods_inspect import OfferBlock, read_kostenaufstellung


def _block_total(block: OfferBlock) -> Decimal:
    return sum(
        (r.value_at(2) or Decimal(0) for r in block.rows_positions),
        Decimal(0),
    )


def diff(gt_path: Path, out_path: Path) -> int:
    gt = read_kostenaufstellung(gt_path)
    actual = read_kostenaufstellung(out_path)

    lines: list[str] = []
    lines.append(f"GROUND TRUTH: {gt_path}  ({len(gt)} blocks)")
    lines.append(f"TOOL OUTPUT:  {out_path}  ({len(actual)} blocks)")
    lines.append("")

    if len(gt) != len(actual):
        lines.append(f"⚠ block count mismatch: gt={len(gt)} vs. tool={len(actual)}")

    for i, (g, a) in enumerate(zip(gt, actual, strict=False), start=1):
        lines.append(f"--- block #{i} ---")
        lines.append(f"  gt header:   {g.soll_header[:90]}")
        lines.append(f"  tool header: {a.soll_header[:90]}")
        lines.append(f"  gt positions: {len(g.rows_positions)}   tool positions: {len(a.rows_positions)}")
        lines.append(f"  gt total:     {_block_total(g)}")
        lines.append(f"  tool total:   {_block_total(a)}")
        diff_total = _block_total(g) - _block_total(a)
        lines.append(f"  Δ total:      {diff_total}")
        if g.row_sum and a.row_sum:
            lines.append(f"  gt Σ-row inv/neb/nac: {g.row_sum.value_at(3)} / {g.row_sum.value_at(4)} / {g.row_sum.value_at(5)}")
            lines.append(f"  tool Σ-row inv/neb/nac: {a.row_sum.value_at(3)} / {a.row_sum.value_at(4)} / {a.row_sum.value_at(5)}")
        lines.append("")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m tests.diff_kostenaufstellung <ground_truth.ods> <tool_output.ods>")
        sys.exit(2)
    raise SystemExit(diff(Path(sys.argv[1]), Path(sys.argv[2])))
