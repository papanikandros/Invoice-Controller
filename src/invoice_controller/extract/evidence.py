"""Per-field evidence (E1, 2026-10-06; analysis in PLAN.md §"Per-field evidence").

Every value that matters for the Verwendungsnachweis — vendor, invoice number, the two
dates the fundability rules hang on, netto, brutto, the Bescheid dates — carries
`Evidence(quote, status, page)`. The LLM returns a verbatim quote per field; THIS module
grounds it deterministically in the text the model actually read (zz-scraper's
cite-then-check idea: the model cites, code checks):

  verified             quote found in the source text AND the value is inside the quote
  value-not-in-quote   quote found, but the value is not in it (the model "read" something
                       the quote does not say — the `A.04.2026` → 01.04.2026 case)
  quote-not-found      the model returned a quote the source text does not contain
  unquoted             value without a quote
  absent               no value (honest null)
  unverifiable-scan    vision path: there is no text to ground against
  xml                  ZUGFeRD/Factur-X: machine data, no quote needed

Grounding tolerances come from the 24-invoice experiment: German AND English date
spellings, digit groups split by spaces ("€ 7 8 402,00"), OCR text whose spacing differs
from the model's cleaned quote (digits-only / token-subsequence location).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

VERIFIED = "verified"
VALUE_NOT_IN_QUOTE = "value-not-in-quote"
QUOTE_NOT_FOUND = "quote-not-found"
UNQUOTED = "unquoted"
ABSENT = "absent"
UNVERIFIABLE_SCAN = "unverifiable-scan"
XML = "xml"
UNRESOLVED = "unresolved"       # E2: the verify turn could not confirm the value either
FROM_FILENAME = "from-filename"  # vendor taken from the colleague's own filename (image letterhead)

TRUSTED = frozenset({VERIFIED, XML})
# statuses that mean "the extraction could not vouch for this value on a readable document"
DOUBTFUL = frozenset({VALUE_NOT_IN_QUOTE, QUOTE_NOT_FOUND, UNQUOTED, UNRESOLVED})

STATUS_DE = {
    VERIFIED: "belegt",
    VALUE_NOT_IN_QUOTE: "Wert nicht im Zitat",
    QUOTE_NOT_FOUND: "Zitat nicht im Text",
    UNQUOTED: "ohne Zitat",
    ABSENT: "nicht angegeben",
    UNVERIFIABLE_SCAN: "Scan — nicht prüfbar",
    XML: "E-Rechnung (XML)",
    UNRESOLVED: "unbestätigt",
    FROM_FILENAME: "aus Dateiname",
}

INVOICE_EVIDENCE_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "order_date", "netto", "brutto")


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    quote: str | None = None
    page: int | None = None
    note: str | None = None

    @property
    def trusted(self) -> bool:
        return self.status in TRUSTED

    @property
    def doubtful(self) -> bool:
        return self.status in DOUBTFUL


# --- normalisation --------------------------------------------------------------------

_MONTHS_DE = ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august",
              "september", "oktober", "november", "dezember"]
_MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december"]


def loose(text: str) -> str:
    """Letters/digits only, single-spaced, lowercase — tolerant of layout, line breaks,
    punctuation and OCR noise around words."""
    return re.sub(r"[^a-z0-9äöüß]+", " ", text.lower()).strip()


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def date_variants(d: date) -> set[str]:
    """Every spelling a printed date can take, in `loose` form."""
    out = {
        d.strftime("%d.%m.%Y"), d.strftime("%d.%m.%y"), f"{d.day}.{d.month}.{d.year}",
        d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%Y%m%d"),
        f"{d.day}. {_MONTHS_DE[d.month - 1]} {d.year}", f"{d.day} {_MONTHS_DE[d.month - 1]} {d.year}",
        f"{_MONTHS_EN[d.month - 1]} {d.day} {d.year}", f"{d.day} {_MONTHS_EN[d.month - 1]} {d.year}",
        f"{_MONTHS_EN[d.month - 1]} {d.day}th {d.year}", f"{_MONTHS_EN[d.month - 1]} {d.day}st {d.year}",
        f"{_MONTHS_EN[d.month - 1]} {d.day}nd {d.year}", f"{_MONTHS_EN[d.month - 1]} {d.day}rd {d.year}",
    }
    return {loose(v) for v in out}


def value_in_quote(value, quote: str) -> bool:
    """Is the extracted value stated in the quote? Dates by spelling variants, numbers by
    their digit string (so "7 8 402,00" matches 78402.00 and "1882 25 91" matches
    18822591), text by loose containment or ≥ half of its tokens."""
    q = loose(quote)
    if isinstance(value, date):
        return any(v in q for v in date_variants(value)) or digits(value.strftime("%d%m%Y")) in digits(quote)
    if isinstance(value, Decimal):
        cents = digits(f"{abs(value):.2f}")
        whole = digits(str(abs(value).quantize(Decimal(1))))
        qd = digits(quote)
        return cents in qd or (whole and whole in qd and value == value.to_integral_value())
    text = loose(str(value))
    if not text:
        return False
    if text in q or digits(text) and len(digits(text)) >= 5 and digits(text) in digits(quote):
        return True
    toks = [t for t in text.split() if len(t) > 2]
    return bool(toks) and sum(1 for t in toks if t in q) / len(toks) >= 0.5


def locate(quote: str, pages: list[str]) -> int | None:
    """Page (1-based) on which the quote occurs, tolerant of layout and OCR spacing:
    exact loose containment first, then the quote's digit string (numbers survive OCR
    better than letters), then ≥ 80 % of its tokens in order."""
    lq = loose(quote)
    if not lq:
        return None
    for i, page in enumerate(pages, start=1):
        if lq in loose(page):
            return i
    qd = digits(quote)
    if len(qd) >= 6:
        for i, page in enumerate(pages, start=1):
            if qd in digits(page):
                return i
    toks = [t for t in lq.split() if len(t) > 1]
    if len(toks) >= 3:
        for i, page in enumerate(pages, start=1):
            lp = loose(page)
            hits = sum(1 for t in toks if t in lp)
            if hits / len(toks) >= 0.8:
                return i
    return None


def ground(value, quote: str | None, pages: list[str] | None, *, method: str = "") -> Evidence:
    """Status for one field. `pages` None/empty = vision path (nothing to ground against)."""
    if method.startswith("zugferd"):
        return Evidence(status=XML)
    if value is None:
        return Evidence(status=ABSENT)
    if not pages or not any(p.strip() for p in pages):
        return Evidence(status=UNVERIFIABLE_SCAN, quote=quote)
    if not quote or not quote.strip():
        return Evidence(status=UNQUOTED)
    page = locate(quote, pages)
    if page is None:
        return Evidence(status=QUOTE_NOT_FOUND, quote=quote)
    if not value_in_quote(value, quote):
        return Evidence(status=VALUE_NOT_IN_QUOTE, quote=quote, page=page)
    return Evidence(status=VERIFIED, quote=quote, page=page)


def ground_fields(obj, fields: tuple[str, ...], pages: list[str] | None, *, method: str = "") -> dict[str, Evidence]:
    """`obj` carries `<field>` and `<field>_quote` attributes (the LLM output)."""
    return {
        f: ground(getattr(obj, f, None), getattr(obj, f"{f}_quote", None), pages, method=method)
        for f in fields
    }


def describe(field_label: str, ev: Evidence) -> str:
    """One German line for flags/comments: 'Rechnungsdatum: belegt (S. 1: "Datum: 21.07.2026")'."""
    text = f"{field_label}: {STATUS_DE.get(ev.status, ev.status)}"
    if ev.quote:
        where = f"S. {ev.page}: " if ev.page else ""
        text += f' ({where}"{ev.quote[:80]}")'
    return text
