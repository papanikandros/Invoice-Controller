"""R7 — the evaluation metrics themselves (typed equality, pairing, scoring)."""

from __future__ import annotations

from decimal import Decimal

from tests.harness.metrics import (
    FieldScore,
    amounts_equal,
    dates_equal,
    merge_field_scores,
    merge_line_reports,
    score_fields,
    score_line_items,
    text_overlap,
    texts_equal,
)


class TestTypedEquality:
    def test_amounts_format_agnostic(self) -> None:
        assert amounts_equal(Decimal("1234.56"), 1234.56)
        assert amounts_equal("1.234,56", Decimal("1234.56"))
        assert amounts_equal(Decimal("1234.56"), Decimal("1234.57"))   # inside tolerance
        assert not amounts_equal(Decimal("1234.56"), Decimal("1234.60"))
        assert amounts_equal(None, None)
        assert not amounts_equal(None, Decimal("1"))

    def test_dates_across_formats(self) -> None:
        from datetime import date
        assert dates_equal(date(2026, 3, 15), "2026-03-15")
        assert dates_equal("15.03.2026", "2026-03-15 00:00:00")
        assert not dates_equal("15.03.2026", "16.03.2026")

    def test_texts_normalized(self) -> None:
        assert texts_equal("Müller GmbH", "mueller gmbh")
        assert texts_equal("Wärme-Pumpe  (neu)", "waerme pumpe neu")
        assert not texts_equal("Zimmerei", "Dachdecker")

    def test_overlap(self) -> None:
        assert text_overlap("Montage der Wärmepumpe", "Wärmepumpe Montage") > 0.5
        assert text_overlap("abc", "xyz") == 0.0


class TestFieldScoring:
    def test_gt_drives_the_keyset_and_types(self) -> None:
        scores = score_fields(
            {"netto": Decimal("100.00"), "invoice_date": "2026-01-02", "vendor_name": "Muster GmbH"},
            {"netto": "100,00", "invoice_date": "02.01.2026", "vendor_name": "MUSTER GmbH", "brutto": Decimal("119")},
            amount_fields={"netto", "brutto"},
            date_fields={"invoice_date"},
        )
        assert scores["netto"].accuracy == 1.0
        assert scores["invoice_date"].accuracy == 1.0
        assert scores["vendor_name"].accuracy == 1.0
        assert scores["brutto"].accuracy == 0.0 and scores["brutto"].mismatches

    def test_merge(self) -> None:
        a = {"netto": FieldScore(total=2, correct=1)}
        merge_field_scores(a, {"netto": FieldScore(total=1, correct=1)})
        assert a["netto"].total == 3 and a["netto"].correct == 2


class TestLineItems:
    GT = [
        {"pos": "1", "description": "Schraubenverdichter SV-90", "line_total_net": "42300.00"},
        {"pos": "2", "description": "Montage und Inbetriebnahme", "line_total_net": "3800.00"},
        {"pos": "", "description": "Rabatt", "line_total_net": "-1500.00"},
    ]

    def test_perfect_extraction(self) -> None:
        r = score_line_items(list(self.GT), list(self.GT))
        assert (r.matched, r.missing, r.spurious) == (3, 0, 0)
        assert r.amount_accuracy == 1.0 and r.recall == 1.0 and r.f_beta() == 1.0

    def test_pairing_survives_reordering_and_wording(self) -> None:
        pred = [
            {"pos": "2", "description": "Inbetriebnahme & Montage", "line_total_net": "3800.00"},
            {"pos": "", "description": "Rabatt auf Warenwert", "line_total_net": "-1500.00"},
            {"pos": "1", "description": "Verdichter SV-90", "line_total_net": "42300.00"},
        ]
        r = score_line_items(pred, list(self.GT))
        assert (r.matched, r.missing, r.spurious) == (3, 0, 0)

    def test_dropped_position_is_missing_and_hurts_recall_more(self) -> None:
        pred = self.GT[:2]
        r = score_line_items(list(pred), list(self.GT))
        assert (r.matched, r.missing, r.spurious) == (2, 1, 0)
        assert r.recall < 1.0
        assert any("FEHLT" in d for d in r.details)
        # spurious costs less than missing under β=2
        r_spurious = score_line_items(list(self.GT) + [
            {"pos": "9", "description": "Erfundene Zeile", "line_total_net": "1.00"}
        ], list(self.GT))
        assert r_spurious.f_beta() > r.f_beta()

    def test_amount_error_on_matched_pair(self) -> None:
        pred = [dict(self.GT[0], line_total_net="423000.00")] + [dict(x) for x in self.GT[1:]]
        r = score_line_items(pred, list(self.GT))
        assert r.matched == 3
        assert r.amount_accuracy < 1.0
        assert any("Betrag erwartet" in d for d in r.details)

    def test_merge(self) -> None:
        a = score_line_items(list(self.GT), list(self.GT))
        b = score_line_items(list(self.GT[:2]), list(self.GT))
        merge_line_reports(a, b)
        assert a.matched == 5 and a.missing == 1

    def test_datetime_normalized_to_date(self) -> None:
        from datetime import date, datetime
        assert dates_equal(datetime(2023, 3, 29, 0, 0), date(2023, 3, 29))
