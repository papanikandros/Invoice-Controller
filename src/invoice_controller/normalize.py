from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}

# Grouped ("82.440,00") OR plain ("82440") digits — a typed 82440 must not be read as
# its first three digits (EK4_333 live finding, 2026-09-23: Förderbetrag 824 €).
_DECIMAL_RE = re.compile(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?")
_DATE_NUMERIC_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_DATE_NAMED_RE = re.compile(
    r"(\d{1,2})\.\s*(" + "|".join(GERMAN_MONTHS) + r")\s+(\d{4})",
    re.IGNORECASE,
)


def parse_de_decimal(text: str) -> Decimal:
    """Parse a German-formatted number like '1.234,56' into Decimal('1234.56')."""
    match = _DECIMAL_RE.search(text)
    if not match:
        raise ValueError(f"no decimal found in {text!r}")
    raw = match.group(0)
    cleaned = raw.replace(".", "").replace(",", ".")
    return Decimal(cleaned)


def parse_de_date(text: str) -> date:
    """Parse 'DD.MM.YYYY' or 'DD. Monat YYYY' into a date."""
    text = text.strip()
    if m := _DATE_NUMERIC_RE.search(text):
        d, mo, y = (int(m.group(i)) for i in (1, 2, 3))
        return date(y, mo, d)
    if m := _DATE_NAMED_RE.search(text):
        d = int(m.group(1))
        mo = GERMAN_MONTHS[m.group(2).lower()]
        y = int(m.group(3))
        return date(y, mo, d)
    raise ValueError(f"unrecognised date format: {text!r}")


def normalize_text(text: str) -> str:
    """NFC-normalise, reattach soft-hyphenated compound words, repair umlauts."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)
    return text


def format_de_decimal(value: Decimal, places: int = 2) -> str:
    """Format Decimal('1234.56') as '1.234,56'."""
    quantized = value.quantize(Decimal(10) ** -places)
    sign = "-" if quantized < 0 else ""
    quantized = abs(quantized)
    int_part, _, frac_part = f"{quantized:.{places}f}".partition(".")
    int_with_dots = ""
    while len(int_part) > 3:
        int_with_dots = "." + int_part[-3:] + int_with_dots
        int_part = int_part[:-3]
    int_with_dots = int_part + int_with_dots
    return f"{sign}{int_with_dots},{frac_part}"


def format_seiten(pages: list[int]) -> str:
    """Format a page list as a German citation fragment: [2,3] -> 'Seite 2 und 3',
    [3] -> 'Seite 3', [2,3,5] -> 'Seite 2, 3 und 5'. Empty -> ''. Deduplicated and sorted."""
    uniq = sorted({p for p in pages if p > 0})
    if not uniq:
        return ""
    nums = [str(p) for p in uniq]
    joined = nums[0] if len(nums) == 1 else f"{', '.join(nums[:-1])} und {nums[-1]}"
    # The consultant's house style uses singular "Seite" even for multiple pages
    # ("Seite 2 und 3"), so we don't pluralise.
    return f"Seite {joined}"
