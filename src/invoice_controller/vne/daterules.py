"""The two fundability date rules (consultant, 2026-10-04) — one implementation for EEW
and BEG (V7: "the date rules are the same as in EEW", 2026-10-05):

  Auftrag erteilt  ≥ Antragstellung                            (always)
  Rechnungsdatum   ≥ Zuwendungsbescheid datiert                (no AavM)
  Rechnungsdatum   ≥ AavM-Genehmigung                          (AavM granted)

A missing date never fails a check (nobody can validate what no document states);
False means the invoice is worthless for the Verwendungsnachweis. The sheet formulas
(`vne/xlsx.py`, `beg/xlsx.py`) encode exactly the same three lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DateRules:
    antragstellung: date | None = None
    aavm_genehmigung: date | None = None
    bescheid_datiert: date | None = None

    @property
    def rechnung_untergrenze(self) -> date | None:
        return self.aavm_genehmigung or self.bescheid_datiert

    def auftrag_ok(self, order_date: date | None) -> bool | None:
        if order_date is None or self.antragstellung is None:
            return None
        return order_date >= self.antragstellung

    def rechnung_ok(self, invoice_date: date | None) -> bool | None:
        bound = self.rechnung_untergrenze
        if bound is None or invoice_date is None:
            return None
        return invoice_date >= bound

    def fundable(self, order_date: date | None, invoice_date: date | None) -> bool:
        return self.auftrag_ok(order_date) is not False and self.rechnung_ok(invoice_date) is not False

    @property
    def is_empty(self) -> bool:
        return self.antragstellung is None and self.aavm_genehmigung is None and self.bescheid_datiert is None
