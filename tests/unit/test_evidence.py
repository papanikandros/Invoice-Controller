"""E1 — per-field evidence: grounding statuses and the normaliser cases the 2026-10-06
experiment exposed (English month names, spaced digit groups, OCR spacing)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from invoice_controller.extract.evidence import (
    ABSENT,
    QUOTE_NOT_FOUND,
    UNQUOTED,
    UNVERIFIABLE_SCAN,
    VALUE_NOT_IN_QUOTE,
    VERIFIED,
    XML,
    ground,
    locate,
    value_in_quote,
)

PAGE = (
    "L & R Kältetechnik GmbH & Co.KG\nSCHLUSSRECHNUNG\nBelegnummer: RG0018867\n"
    "Datum: 21.07.2026\nWir berechnen Ihnen laut unserer Auftragsbestätigung Nr. 26-4132 vom 27.03.2026\n"
    "Nettobetrag 15.640,00 €\nSubtotal          €   7 8 402,00\nInvoice date June 5th 2025\n"
)


class TestValueInQuote:
    def test_dates_in_german_english_and_iso(self) -> None:
        d = date(2025, 6, 5)
        for q in ("Datum: 05.06.2025", "5.6.2025", "June 5th 2025", "5. Juni 2025", "2025-06-05", "05/06/2025"):
            assert value_in_quote(d, q), q
        assert not value_in_quote(d, "A.06.2025")                    # the hallucinated-day case
        assert not value_in_quote(date(2026, 4, 1), "A.04.2026")

    def test_amounts_survive_spaced_digit_groups(self) -> None:
        assert value_in_quote(Decimal("78402.00"), "Subtotal € 7 8 402,00")
        assert value_in_quote(Decimal("15640.00"), "Nettobetrag 15.640,00 €")
        assert value_in_quote(Decimal("18424"), "Total EURO € 1 8 424.00")
        assert not value_in_quote(Decimal("15640.00"), "Nettobetrag 15.460,00 €")

    def test_numbers_and_text(self) -> None:
        assert value_in_quote("18822591", "Rechnung NR. 1882 25 91")
        assert value_in_quote("L & R Kältetechnik GmbH & Co.KG", "L&R Kältetechnik GmbH & Co. KG")
        assert not value_in_quote("Franz Kempmann Transport", "MKT GmbH Rechnungsadresse")


class TestLocate:
    def test_exact_and_fuzzy(self) -> None:
        assert locate("Belegnummer: RG0018867", [PAGE]) == 1
        assert locate("Datum:\n21.07.2026", [PAGE]) == 1               # OCR line break in the quote
        assert locate("Rechnungs-Nr. 26-4132 vom 27.03.2026", [PAGE]) == 1   # digits locate it
        assert locate("Steuernummer 123/456", [PAGE]) is None


class TestGround:
    def test_statuses(self) -> None:
        assert ground(date(2026, 7, 21), "Datum: 21.07.2026", [PAGE]).status == VERIFIED
        assert ground(date(2026, 7, 21), "Datum: 21.07.2026", [PAGE]).page == 1
        assert ground(date(2026, 7, 22), "Datum: 21.07.2026", [PAGE]).status == VALUE_NOT_IN_QUOTE
        assert ground(date(2026, 7, 22), "Rechnungsdatum 22.07.2026 Herford", [PAGE]).status == QUOTE_NOT_FOUND
        assert ground(date(2026, 7, 21), None, [PAGE]).status == UNQUOTED
        assert ground(None, None, [PAGE]).status == ABSENT
        assert ground(date(2026, 7, 21), "Datum: 21.07.2026", None).status == UNVERIFIABLE_SCAN
        assert ground(date(2026, 7, 21), None, [PAGE], method="zugferd-xml").status == XML
