"""Type-aware extraction metrics (R7).

Design taken from the 2026-08 literature survey:
- VRDU's typed matching: an amount matches numerically (tolerance, sign, format
  agnostic), a date matches as a parsed date, a string matches after
  normalization — never raw string equality across types.
- GLIRM-style line items: predictions and ground truth are PAIRED first (greedy
  on a weighted similarity — amounts dominate, description overlap second, the
  printed position number third), then fields are scored over the pairs;
  unpaired rows count as missing (GT) / spurious (prediction). Recall-weighted:
  for a funding audit a dropped position is worse than an extra one.

The metric never mutates inputs and needs no LLM — it scores whatever the caller
extracted, so the same functions serve regression tests and A/B benchmarks.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

AMOUNT_TOLERANCE = Decimal("0.01")

# --- typed field equality ---------------------------------------------------------------


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _as_amount(v: Any) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    try:
        return Decimal(str(v).replace(".", "").replace(",", ".")) if re.search(
            r"\d,\d\d$", str(v)
        ) else Decimal(str(v))
    except InvalidOperation:
        return None


def amounts_equal(a: Any, b: Any, tolerance: Decimal = AMOUNT_TOLERANCE) -> bool:
    da, db = _as_amount(a), _as_amount(b)
    if da is None or db is None:
        return da is db
    return abs(da - db) <= tolerance


def dates_equal(a: Any, b: Any) -> bool:
    def parse(v: Any) -> date | None:
        from datetime import datetime

        if isinstance(v, datetime):     # datetime subclasses date — normalize first
            return v.date()
        if isinstance(v, date):
            return v
        if v is None:
            return None
        text = str(v)[:10]
        for pattern, order in ((r"(\d{4})-(\d{2})-(\d{2})", (1, 2, 3)),
                               (r"(\d{2})\.(\d{2})\.(\d{4})", (3, 2, 1))):
            m = re.match(pattern, text)
            if m:
                y, mo, d = (int(m.group(i)) for i in order)
                try:
                    return date(y, mo, d)
                except ValueError:
                    return None
        return None

    return parse(a) == parse(b)


def texts_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    return _norm_text(a) == _norm_text(b)


def text_overlap(a: Any, b: Any) -> float:
    """Token-level Jaccard on normalized text — the description-similarity signal."""
    ta, tb = set(_norm_text(a or "").split()), set(_norm_text(b or "").split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# --- header fields ----------------------------------------------------------------------


@dataclass
class FieldScore:
    total: int = 0
    correct: int = 0
    mismatches: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0


def score_fields(
    predicted: dict[str, Any],
    expected: dict[str, Any],
    *,
    amount_fields: set[str] = frozenset(),
    date_fields: set[str] = frozenset(),
    label: str = "",
) -> dict[str, FieldScore]:
    """Score every key of `expected` (the GT decides what counts). Missing predicted
    values are wrong unless the GT is also None."""
    scores: dict[str, FieldScore] = {}
    for key, want in expected.items():
        got = predicted.get(key)
        if key in amount_fields:
            ok = amounts_equal(got, want)
        elif key in date_fields:
            ok = dates_equal(got, want)
        else:
            ok = texts_equal(got, want)
        s = scores.setdefault(key, FieldScore())
        s.total += 1
        if ok:
            s.correct += 1
        else:
            s.mismatches.append(f"{label}{key}: erwartet {want!r}, extrahiert {got!r}")
    return scores


def merge_field_scores(into: dict[str, FieldScore], add: dict[str, FieldScore]) -> None:
    for key, s in add.items():
        target = into.setdefault(key, FieldScore())
        target.total += s.total
        target.correct += s.correct
        target.mismatches.extend(s.mismatches)


# --- line items -------------------------------------------------------------------------


@dataclass
class LineItemReport:
    matched: int = 0
    missing: int = 0          # ground-truth rows with no counterpart (the bad case)
    spurious: int = 0         # predicted rows with no counterpart
    amount_correct: int = 0
    description_similarity: float = 0.0     # mean Jaccard over matched pairs
    pairs: list[tuple[int, int, float]] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        gt = self.matched + self.missing
        return self.matched / gt if gt else 1.0

    @property
    def precision(self) -> float:
        pred = self.matched + self.spurious
        return self.matched / pred if pred else 1.0

    @property
    def amount_accuracy(self) -> float:
        return self.amount_correct / self.matched if self.matched else 1.0

    def f_beta(self, beta: float = 2.0) -> float:
        """Recall-weighted F (GLIRM habit: β>1 — dropping a position is worse)."""
        p, r = self.precision, self.recall
        if p == 0 and r == 0:
            return 0.0
        b2 = beta * beta
        return (1 + b2) * p * r / (b2 * p + r)


def _pair_score(pred: dict[str, Any], gt: dict[str, Any]) -> float:
    score = 0.0
    if amounts_equal(pred.get("line_total_net"), gt.get("line_total_net")):
        score += 0.6
    score += 0.3 * text_overlap(pred.get("description"), gt.get("description"))
    if pred.get("pos") and gt.get("pos") and str(pred["pos"]).strip() == str(gt["pos"]).strip():
        score += 0.1
    return score


def score_line_items(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    *,
    min_pair_score: float = 0.25,
    label: str = "",
) -> LineItemReport:
    """Greedy globally-best pairing (list sizes here are tens, not thousands), then
    typed scoring over the pairs."""
    report = LineItemReport()
    candidates = sorted(
        (
            (_pair_score(p, g), i, j)
            for i, p in enumerate(predicted)
            for j, g in enumerate(expected)
        ),
        reverse=True,
    )
    used_pred: set[int] = set()
    used_gt: set[int] = set()
    sims: list[float] = []
    for score, i, j in candidates:
        if score < min_pair_score or i in used_pred or j in used_gt:
            continue
        used_pred.add(i)
        used_gt.add(j)
        report.matched += 1
        report.pairs.append((i, j, score))
        if amounts_equal(predicted[i].get("line_total_net"), expected[j].get("line_total_net")):
            report.amount_correct += 1
        else:
            report.details.append(
                f"{label}Pos {expected[j].get('pos', j)}: Betrag erwartet "
                f"{expected[j].get('line_total_net')}, extrahiert {predicted[i].get('line_total_net')}"
            )
        sims.append(text_overlap(predicted[i].get("description"), expected[j].get("description")))
    report.missing = len(expected) - len(used_gt)
    report.spurious = len(predicted) - len(used_pred)
    for j, g in enumerate(expected):
        if j not in used_gt:
            report.details.append(f"{label}FEHLT: Pos {g.get('pos', j)} — {str(g.get('description'))[:40]}")
    report.description_similarity = sum(sims) / len(sims) if sims else 0.0
    return report


def merge_line_reports(into: LineItemReport, add: LineItemReport) -> None:
    total_matched = into.matched + add.matched
    if total_matched:
        into.description_similarity = (
            into.description_similarity * into.matched + add.description_similarity * add.matched
        ) / total_matched
    into.matched = total_matched
    into.missing += add.missing
    into.spurious += add.spurious
    into.amount_correct += add.amount_correct
    into.details.extend(add.details)
