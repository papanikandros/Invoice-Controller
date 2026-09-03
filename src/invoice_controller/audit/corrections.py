"""R8 — machine-readable consultant corrections (CloudScan's distant-supervision idea).

The consultant verifies a generated Kostenaufstellung in Excel/LibreOffice and edits
what extraction got wrong. Those corrections are the most valuable quality data the
workflow produces — this module diffs the generated file against the corrected copy
and emits typed records (`korrekturen.jsonl`), so they accumulate as eval/tuning
data instead of evaporating into the edited file.

Semantic diff, not cell diff: SOLL blocks matched by header, positions matched by
Pos. number (falling back to description), amounts compared as Decimals. Formula
cells are read via cached values — a consultant-saved file always carries them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl

_AMOUNT_FIELDS = ("gesamt", "investitionskosten", "nebenkosten")


@dataclass
class PositionRow:
    pos: str
    description: str
    gesamt: Decimal | None
    investitionskosten: Decimal | None
    nebenkosten: Decimal | None


@dataclass
class Correction:
    """One consultant change. scope: position-field | position-added |
    position-removed | block-added | block-removed."""

    scope: str
    block: str
    pos: str | None = None
    field: str | None = None
    original: str | None = None
    corrected: str | None = None


def _dec(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def read_blocks(path: str | Path) -> dict[str, list[PositionRow]]:
    """SOLL-block → position rows, using the same row grammar as the writers:
    a block starts at 'SOLL…', ends at its 'Σ' row; Sonderpreis/Nettosumme/warning
    rows are not positions."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    blocks: dict[str, list[PositionRow]] = {}
    header: str | None = None
    in_positions = False
    for row in ws.iter_rows(min_col=1, max_col=5):
        first = row[0].value.strip() if isinstance(row[0].value, str) else ""
        second = row[1].value if len(row) > 1 else None
        if first.startswith("SOLL"):
            header = first
            blocks[header] = []
            in_positions = True
            continue
        if header is None or not in_positions:
            continue
        if first.startswith("Σ"):
            in_positions = False
            continue
        if isinstance(second, str) and (
            second.startswith("Sonderpreis") or second.startswith("Nettosumme lt. Dokument")
        ):
            continue
        if first in ("", "Position") and not isinstance(second, str):
            continue
        if first == "Position":
            continue
        blocks[header].append(
            PositionRow(
                pos=first,
                description=str(second or "").strip(),
                gesamt=_dec(row[2].value),
                investitionskosten=_dec(row[3].value),
                nebenkosten=_dec(row[4].value),
            )
        )
    return blocks


def _key(row: PositionRow) -> str:
    return row.pos.strip() or row.description.strip().lower()


def collect_corrections(original: str | Path, corrected: str | Path) -> list[Correction]:
    orig_blocks = read_blocks(original)
    corr_blocks = read_blocks(corrected)
    corrections: list[Correction] = []

    for header, orig_rows in orig_blocks.items():
        if header not in corr_blocks:
            corrections.append(Correction(scope="block-removed", block=header))
            continue
        corr_rows = {_key(r): r for r in corr_blocks[header]}
        seen: set[str] = set()
        for row in orig_rows:
            key = _key(row)
            match = corr_rows.get(key)
            if match is None:
                corrections.append(
                    Correction(scope="position-removed", block=header, pos=row.pos,
                               original=row.description)
                )
                continue
            seen.add(key)
            if row.description.strip() != match.description.strip():
                corrections.append(
                    Correction(scope="position-field", block=header, pos=row.pos,
                               field="description",
                               original=row.description, corrected=match.description)
                )
            for field in _AMOUNT_FIELDS:
                a, b = getattr(row, field), getattr(match, field)
                if a != b:
                    corrections.append(
                        Correction(scope="position-field", block=header, pos=row.pos,
                                   field=field,
                                   original=str(a) if a is not None else None,
                                   corrected=str(b) if b is not None else None)
                    )
        for key, row in corr_rows.items():
            if key not in seen:
                corrections.append(
                    Correction(scope="position-added", block=header, pos=row.pos,
                               corrected=row.description)
                )
    for header in corr_blocks:
        if header not in orig_blocks:
            corrections.append(Correction(scope="block-added", block=header))
    return corrections


def write_corrections_jsonl(corrections: list[Correction], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    with out_path.open("w", encoding="utf-8") as fh:
        for c in corrections:
            fh.write(json.dumps({k: v for k, v in asdict(c).items() if v is not None},
                                ensure_ascii=False) + "\n")
    return out_path
