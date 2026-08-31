"""Verbatim-amount grounding (R2) — a cheap anti-hallucination guard before the cross-sum.

Every amount the LLM claims is STATED on the document must appear verbatim (in German
number format) in the text the LLM actually read. Amounts that don't are reported so
the row can be demoted into the consultant's must-review bucket — never blocked, the
same loud-not-silent rule as the cross-sum.

Scope, per the 2026-08-31 corpus benchmark (208 ground-truth amounts, 10 projects:
100 % of genuinely printed amounts on text-layer PDFs are findable verbatim; misses
were scans and consultant-DERIVED figures):
- runs only when source text exists (pdfplumber or Tesseract tier) — the vision path
  has no local text to ground against;
- checks only stated fields (netto, brutto, MwSt, cumulative/advance totals, position
  line totals) — never derived values;
- a miss is a flag, not a failure (Hemmer et al., IJDAR 2025: genuine documents can
  be arithmetically wrong; formatting oddities exist)."""

from __future__ import annotations

from decimal import Decimal

from invoice_controller.models import AmountCheck
from invoice_controller.normalize import format_de_decimal


def _variants(amount: Decimal) -> list[str]:
    """German-format spellings a printed amount can plausibly take in extracted text:
    grouped (1.234,56), space-grouped, ungrouped — each also unsigned, because credit
    notes print amounts without the sign our models carry."""
    out: list[str] = []
    for value in {amount, abs(amount)}:
        de = format_de_decimal(value)
        out += [de, de.replace(".", " "), de.replace(".", "")]
    return out


def check_amounts_grounded(
    amounts: dict[str, Decimal | None], source_text: str
) -> AmountCheck:
    """`amounts` maps a field label (rendered into the message) to its extracted value;
    None values are skipped — absent fields have nothing to ground."""
    missing = [
        label
        for label, amount in amounts.items()
        if amount is not None and not any(v in source_text for v in _variants(amount))
    ]
    if not missing:
        return AmountCheck(passed=True)
    return AmountCheck(
        passed=False,
        message=f"nicht wörtlich im Text gefunden: {', '.join(missing)}",
    )
