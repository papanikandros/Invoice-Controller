"""Parse an F1-format ``Kostenaufstellung`` PDF back into structured ground truth.

The consultant's hand-built Kostenaufstellung is shipped in the example corpus
only as a PDF (no source ``.ods``). This reader recovers the numbers the F1
pipeline is expected to reproduce: per-vendor SOLL blocks, each with its Σ
totals row (Gesamt / Investitionskosten / Nebenkosten / Nachlass) and the
percentage split row. Positions are captured best-effort; the totals and the
IK/NK percentage split are the load-bearing assertions (the split feeds F2).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

# German money token, e.g. "413.050,00 €", "-5.332,77", "0,00 €".
_MONEY = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
_PCT = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}\s*%")
# Block titles vary by vendor template: "SOLL:", "SOLL L&R Angebot", "NEU: Mainka",
# "SOLL Angebot.Nr:", "Soll-Nebenostenschätzung:". Anchor on the keyword + an offer cue.
_BLOCK_HEADER = re.compile(
    r"(\b(SOLL|NEU)\b.*(Angebot|vom|Schätzung|Nebenkosten|Nebenosten))"
    r"|(Sch[äa]tzung|Stellungnahme)",
    re.IGNORECASE,
)
# Grand-total line, label varies: "Gesamtpreis Pos", "Summe 1. Einzelangebot",
# "Angebotspreis vom", "Nettosumme", "Gesamtsumme".
_SUM_LINE = re.compile(
    r"Gesamtpreis|Summe|Angebotspreis|Nettosumme|Gesamtsumme|Endbetrag",
    re.IGNORECASE,
)
_SONDER = re.compile(r"Sonderpreis|Sonderkonditionen|Pauschalpreis", re.IGNORECASE)


def _de_money(tok: str) -> Decimal:
    return Decimal(tok.replace(".", "").replace(",", "."))


def _de_pct(tok: str) -> Decimal:
    return Decimal(tok.replace("%", "").strip().replace(".", "").replace(",", "."))


@dataclass
class SollBlock:
    header: str
    totals: list[Decimal] = field(default_factory=list)      # [Gesamt, IK, NK, Nachlass]
    percentages: list[Decimal] = field(default_factory=list)  # [100, IK%, NK%, Nachlass%]
    sonderpreis: Decimal | None = None
    n_position_lines: int = 0

    @property
    def gesamt(self) -> Decimal | None:
        return self.totals[0] if self.totals else None

    @property
    def ik(self) -> Decimal | None:
        return self.totals[1] if len(self.totals) > 1 else None

    @property
    def nk(self) -> Decimal | None:
        return self.totals[2] if len(self.totals) > 2 else None

    @property
    def ik_pct(self) -> Decimal | None:
        return self.percentages[1] if len(self.percentages) > 1 else None

    @property
    def nk_pct(self) -> Decimal | None:
        return self.percentages[2] if len(self.percentages) > 2 else None

    @property
    def ik_ratio(self) -> Decimal | None:
        """IK share as a fraction. From the % row when present, else IK/Gesamt.

        This is the split F2 applies to each of the vendor's invoices.
        """
        if self.ik_pct is not None:
            return self.ik_pct / Decimal(100)
        if self.gesamt and self.ik is not None and self.gesamt != 0:
            return self.ik / self.gesamt
        return None

    @property
    def is_complete(self) -> bool:
        return self.gesamt is not None and self.ik_ratio is not None


@dataclass
class Kostenaufstellung:
    path: Path
    blocks: list[SollBlock] = field(default_factory=list)

    @property
    def is_f1_format(self) -> bool:
        return bool(self.blocks)


def pdf_text(path: str | Path) -> str:
    out = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def parse_kostenaufstellung(path: str | Path) -> Kostenaufstellung:
    path = Path(path)
    lines = pdf_text(path).splitlines()
    result = Kostenaufstellung(path=path)
    current: SollBlock | None = None
    # Totals and the % split can land on separate wrapped lines from the
    # "Gesamtpreis" label, so track the last money-only line before the % row.
    pending_totals: list[Decimal] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        is_position = bool(re.match(r"^\s*\d+[.\d]*\s+\S", line))

        # A block title: keyword + offer cue, and not itself a numbered position line.
        if not is_position and _BLOCK_HEADER.search(stripped):
            current = SollBlock(header=stripped)
            result.blocks.append(current)
            pending_totals = []
            continue

        if current is None:
            continue

        if _SONDER.search(stripped):
            monies = _MONEY.findall(stripped)
            if monies:
                current.sonderpreis = _de_money(monies[-1])
            continue

        # The % split row closes the block summary; adopt the last totals seen.
        pcts = _PCT.findall(stripped)
        if len(pcts) >= 2 and not current.percentages:
            current.percentages = [_de_pct(p) for p in pcts]
            if not current.totals and pending_totals:
                current.totals = pending_totals
            continue

        monies = _MONEY.findall(stripped)

        # Explicit grand-total line with inline amounts (keep the last -> grand total).
        if not is_position and _SUM_LINE.search(stripped) and len(monies) >= 2:
            current.totals = [_de_money(m) for m in monies]
            pending_totals = current.totals
            continue

        # A non-position money-only line right before the % row carries the totals.
        if not is_position and len(monies) >= 2:
            pending_totals = [_de_money(m) for m in monies]
            continue

        if is_position and monies:
            current.n_position_lines += 1

    return result


def find_f1_pdf(project_dir: str | Path) -> Path | None:
    """Return the canonical F1-format Kostenaufstellung PDF for a project.

    Several projects ship both the F1 output ("Kostenaufstellung, <Client>.pdf")
    and raw vendor offers with "Kostenaufstellung" in the name. The F1 output is
    the one whose text carries the "Gesamtpreis Pos." Σ rows.
    """
    project_dir = Path(project_dir)
    candidates = sorted(
        p for p in project_dir.glob("*.pdf")
        if "kostenaufstellung" in p.name.lower()
    )
    # The F1 signature is the IK/NK percentage split row; raw vendor offers lack it.
    scored = []
    for p in candidates:
        ka = parse_kostenaufstellung(p)
        with_pct = [b for b in ka.blocks if b.percentages]
        if with_pct:
            scored.append((len(with_pct), len(ka.blocks), p))
    if not scored:
        return None
    # Most percentage-bearing blocks wins; tie -> the combined ("Kostenaufstellung, X").
    scored.sort(key=lambda t: (-t[0], -t[1], 0 if ", " in t[2].name else 1, len(t[2].name)))
    return scored[0][2]
