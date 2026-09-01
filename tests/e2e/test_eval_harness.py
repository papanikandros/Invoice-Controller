"""R7 — live extraction quality eval (opt-in: `--run-live`).

Scores REAL extractions with the typed metrics (tests/harness/metrics.py) against
the two ground truths that exist at the needed granularity:

- EK4_204 recorded fixtures → offer POSITION-level scoring (pairing, amounts,
  descriptions). atb/munk must stay at ceiling (they were validated perfect);
  L&R only reports — its 11-vs-19 row shape is the known Q5 bundling ambiguity.
- ZePa VNE-Tabelle → invoice HEADER-level scoring (brutto/netto/date), the
  project whose Σ IK was validated to 0,01 €.

Each run writes `tmp/eval_baseline.json` — the comparison artifact for future
prompt/model/OCR changes (R9 uses the same harness).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.metrics import (  # noqa: E402
    LineItemReport,
    amounts_equal,
    merge_line_reports,
    score_fields,
    score_line_items,
    text_overlap,
)

pytestmark = pytest.mark.live

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
BASELINE = Path("tmp") / "eval_baseline.json"

EK4_204_PDFS = {
    "atb": "6. atb Angebot-Soll- Netzanschluss.pdf",
    "munk": "5. Munk Angebot-Soll-Gleichrichteranlage.pdf",
    "lr": "4. L&R Angebot-SOLL-K__lteanlage.pdf",
}


def _write_baseline(section: str, payload: dict) -> None:
    data = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    data[section] = payload
    BASELINE.parent.mkdir(exist_ok=True)
    BASELINE.write_text(json.dumps(data, indent=1, ensure_ascii=False))


@pytest.mark.skipif(not (EXAMPLES / "EK4_204").exists(), reason="corpus absent")
def test_offer_positions_against_ek4_204_fixtures():
    from tests.conftest import load_cases, load_fixture
    from invoice_controller.extract.offer import extract_offer

    per_offer: dict[str, dict] = {}
    overall = LineItemReport()
    for case in load_cases("ek4_204"):
        stem = case["stem"]
        fx = load_fixture("ek4_204", stem)
        offer = extract_offer(EXAMPLES / "EK4_204" / EK4_204_PDFS[stem], with_narrative=False)
        pred = [
            {"pos": p.pos, "description": p.description, "line_total_net": p.line_total_net}
            for p in offer.positions
        ]
        report = score_line_items(pred, fx["positions"], label=f"{stem}/")
        per_offer[stem] = {
            "f_beta2": round(report.f_beta(), 3),
            "recall": round(report.recall, 3),
            "amount_accuracy": round(report.amount_accuracy, 3),
            "description_similarity": round(report.description_similarity, 3),
            "matched/missing/spurious": [report.matched, report.missing, report.spurious],
            "cross_sum": offer.cross_sum.passed,
        }
        merge_line_reports(overall, report)
        print(f"\n{stem}: {per_offer[stem]}")
        for d in report.details[:8]:
            print("   ", d)

    _write_baseline("offer_positions_ek4_204", per_offer)

    # Ceiling contract for the two unambiguous offers; L&R reports only (Q5).
    for stem in ("atb", "munk"):
        assert per_offer[stem]["recall"] == 1.0, per_offer[stem]
        assert per_offer[stem]["amount_accuracy"] == 1.0, per_offer[stem]
    assert per_offer["lr"]["cross_sum"], "L&R must still reconcile its cross-sum"


@pytest.mark.skipif(not (EXAMPLES / "ZePa").exists(), reason="corpus absent")
def test_invoice_headers_against_zepa_vne():
    from tests.corpus import load_project
    from tests.corpus.vne_tabelle import parse_vne_tabelle
    from invoice_controller.extract.invoice import extract_invoice
    from invoice_controller.match.vendor import normalize_vendor

    proj = load_project("ZePa")
    gt = parse_vne_tabelle(proj.vne_xlsx)
    extracted = []
    for pdf in proj.invoices:
        try:
            extracted.append(extract_invoice(pdf))
        except Exception as exc:  # noqa: BLE001 — a corpus scan may defeat the model
            print(f"UNREADABLE {pdf.name}: {type(exc).__name__}")

    # Pair each GT row with the best unconsumed extraction: netto/brutto equality
    # dominates, vendor-name overlap breaks ties.
    used: set[int] = set()
    total = correct_netto = correct_brutto = correct_date = paired = 0
    for row in gt.invoices:
        total += 1
        best, best_score = None, 0.0
        for i, inv in enumerate(extracted):
            if i in used:
                continue
            score = 0.0
            if row.netto is not None and (
                amounts_equal(inv.netto, row.netto)
                or amounts_equal(inv.netto, row.netto_after_skonto)
            ):
                score += 0.6
            if row.brutto is not None and amounts_equal(inv.brutto, row.brutto):
                score += 0.3
            score += 0.1 * text_overlap(inv.vendor_name, row.recipient)
            if score > best_score:
                best, best_score = i, score
        if best is None or best_score < 0.3:
            print(f"kein Match für GT-Zeile {row.recipient} netto={row.netto}")
            continue
        used.add(best)
        paired += 1
        inv = extracted[best]
        s = score_fields(
            {"netto": inv.netto, "brutto": inv.brutto, "invoice_date": inv.invoice_date},
            {"netto": row.netto, "brutto": row.brutto, "invoice_date": row.invoice_date},
            amount_fields={"netto", "brutto"},
            date_fields={"invoice_date"},
            label=f"{row.recipient[:18]}/",
        )
        correct_netto += s["netto"].correct if row.netto is not None else 0
        correct_brutto += s["brutto"].correct if row.brutto is not None else 0
        correct_date += s["invoice_date"].correct if row.invoice_date is not None else 0
        for fs in s.values():
            for m in fs.mismatches:
                print("   ", m)

    payload = {
        "gt_rows": total,
        "paired": paired,
        "netto_correct": correct_netto,
        "brutto_correct": correct_brutto,
        "date_correct": correct_date,
    }
    print(f"\nZePa headers: {payload}")
    _write_baseline("invoice_headers_zepa", payload)

    assert paired >= total * 0.8, f"nur {paired}/{total} GT-Zeilen gepaart"
    assert correct_netto >= paired * 0.8, "Netto-Genauigkeit unter 80 %"
