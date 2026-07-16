from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from invoice_controller.normalize import (
    format_de_decimal,
    format_seiten,
    normalize_text,
    parse_de_date,
    parse_de_decimal,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.234,56", Decimal("1234.56")),
        ("1.234,56 €", Decimal("1234.56")),
        ("18.759,80 EUR", Decimal("18759.80")),
        ("0,00", Decimal("0.00")),
        ("-3,00 %", Decimal("-3.00")),
        ("Endbetrag 129.281,60 EUR", Decimal("129281.60")),
    ],
)
def test_parse_de_decimal(raw: str, expected: Decimal) -> None:
    assert parse_de_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("08.04.2024", date(2024, 4, 8)),
        ("11. Januar 2025", date(2025, 1, 11)),
        ("Datum: 08.04.2024", date(2024, 4, 8)),
        ("11. März 2024", date(2024, 3, 11)),
    ],
)
def test_parse_de_date(raw: str, expected: date) -> None:
    assert parse_de_date(raw) == expected


def test_parse_de_date_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_de_date("not a date")


@pytest.mark.parametrize(
    "value, expected",
    [
        (Decimal("1234.56"), "1.234,56"),
        (Decimal("0"), "0,00"),
        (Decimal("18759.8"), "18.759,80"),
        (Decimal("-3"), "-3,00"),
        (Decimal("1000000"), "1.000.000,00"),
    ],
)
def test_format_de_decimal_roundtrip(value: Decimal, expected: str) -> None:
    formatted = format_de_decimal(value)
    assert formatted == expected
    assert parse_de_decimal(formatted) == value.quantize(Decimal("0.01"))


def test_normalize_text_reattaches_hyphenated_compound() -> None:
    raw = "Anlagen-\nmontage durchgeführt"
    normalized = normalize_text(raw)
    assert "Anlagenmontage" in normalized


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ([], ""),
        ([3], "Seite 3"),
        ([2, 3], "Seite 2 und 3"),
        ([2, 3, 5], "Seite 2, 3 und 5"),
        ([3, 2, 3], "Seite 2 und 3"),  # dedup + sort
    ],
)
def test_format_seiten(pages: list[int], expected: str) -> None:
    assert format_seiten(pages) == expected
