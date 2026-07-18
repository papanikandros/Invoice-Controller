"""Parse the consultant's 'Beschreibung Standort.md' header into a StandortInput.

The header is tab-separated `Label*<tabs>Value` lines (Unternehmen / Straße
Hausnummer / PLZ / Stadt). The same files also carry the reference output prose,
which `parse_expected_text` returns for regression comparison.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import StandortInput

_FIELD_MAP = (
    ("unternehmen", "firma"),
    ("straße", "strasse"),
    ("strasse", "strasse"),
    ("plz", "plz"),
    ("stadt", "stadt"),
)


def _split_label_value(line: str) -> tuple[str, str] | None:
    if "\t" not in line:
        return None
    label, _, rest = line.partition("\t")
    return label.strip().rstrip("*").strip().lower(), rest.replace("\t", " ").strip()


def parse_header(path: str | Path, *, website: str | None = None) -> StandortInput:
    fields: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        parsed = _split_label_value(raw)
        if not parsed:
            continue
        label, value = parsed
        for needle, attr in _FIELD_MAP:
            if label.startswith(needle) and value:
                fields.setdefault(attr, value)
                break
        if label.startswith("1.2"):  # reached the form prompt — header is done
            break
    return StandortInput(
        firma=fields.get("firma", ""),
        strasse=fields.get("strasse", ""),
        plz=fields.get("plz", ""),
        stadt=fields.get("stadt", ""),
        website=website,
    )


def parse_expected_text(path: str | Path) -> str:
    """The reference description prose (from 'Bei d…' onward)."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r"^Bei d.*", text, flags=re.MULTILINE | re.DOTALL)
    return m.group(0).strip() if m else ""
