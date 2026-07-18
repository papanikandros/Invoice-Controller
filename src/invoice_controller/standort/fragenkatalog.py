"""Parse the consultant's 'Fragenkatalog Modul 4' intake PDF into F3 inputs.

The form's filled values live in AcroForm fields with opaque names, and pdfplumber
drops them; `pdftotext -layout` renders every value next to its label (and the ✓
inline after a ticked option), so we parse the layout text by label. The form is
the consultant's stable house template, so label-anchored parsing is reliable.

Supplies firma, address, the separate measure location (preferred for geo), the
Betriebsgesellschaft (Untervermieter), company size (KU/MU/GU → klein/mittel/groß)
and the shift model — the last two replacing F3's earlier LLM/flagged guesses.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import OperationalDefaults, StandortInput

_CHECK = ("✓", "☑", "✔", "x")  # glyphs pdftotext may emit for a ticked box
# Size checkbox: option token (as it appears on the line) → declined adjective stem.
_SIZE_OPTIONS = [("KU/KKU", "kleines"), ("KKU", "kleines"), ("KU", "kleines"),
                 ("MU", "mittleres"), ("GU", "großes")]
_WORD_SHIFT = {"ein": 1, "zwei": 2, "drei": 3}
# Labels that end a multi-line address capture.
_STOP = ("Standort der Maßnahme", "Ansprechpartner", "Betriebsgesellschaft",
         "Art der Anlage", "Das Unternehmen")


@dataclass
class Fragenkatalog:
    firma: str = ""
    strasse: str = ""
    plz: str = ""
    stadt: str = ""
    massnahme_strasse: str | None = None
    massnahme_plz: str | None = None
    massnahme_stadt: str | None = None
    betreiberfirma: str | None = None
    groesse: str = ""             # kleines / mittleres / großes
    schicht_anzahl: int | None = None
    art_der_anlage: str = ""
    vorhaben: str = ""
    wz_code: str = ""
    email: str | None = None


def _pdftotext(path: str | Path) -> str:
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                         capture_output=True, text=True, check=True)
    return out.stdout


def _value_after(lines: list[str], label: str) -> tuple[str, int]:
    """First line containing `label`, returning the text after it (and the line index)."""
    for i, line in enumerate(lines):
        pos = line.find(label)
        if pos == -1:
            continue
        after = line[pos + len(label):].lstrip(" :\t").strip()
        if after:
            return after, i
    return "", -1


def _block_after(lines: list[str], label: str, max_lines: int = 4) -> str:
    """Value that may spill onto following lines (until blank or the next label)."""
    for i, line in enumerate(lines):
        pos = line.find(label)
        if pos == -1:
            continue
        parts = []
        same = line[pos + len(label):].lstrip(" :\t").strip()
        if same:
            parts.append(same)
        for nxt in lines[i + 1:i + 1 + max_lines]:
            s = nxt.strip()
            if not s or ":" in nxt or any(st in nxt for st in _STOP):
                break
            parts.append(s)
        return " ".join(parts).strip()
    return ""


def _parse_addr_oneline(value: str) -> tuple[str | None, str | None, str | None]:
    """'Gersteinstr. 17, 59227 Ahlen' → (street, plz, city)."""
    m = re.search(r"(.*?),?\s*(\d{5})\s+([^\d,]+)$", value.strip())
    if m:
        return m.group(1).strip(" ,") or None, m.group(2), m.group(3).strip()
    return (value.strip() or None), None, None


def _find_plz_city(lines: list[str], start: int) -> tuple[str | None, str | None]:
    for line in lines[start + 1:start + 4]:
        if any(s in line for s in _STOP):
            break
        m = re.match(r"\s*(\d{5})\s+([^\d,]+?)\s*$", line)
        if m:
            return m.group(1), m.group(2).strip()
    return None, None


def _detect_size(lines: list[str]) -> str:
    for line in lines:
        if "Das Unternehmen ist" not in line or "GU" not in line:
            continue
        check_pos = min((line.find(c) for c in _CHECK if c in line), default=-1)
        if check_pos == -1:
            continue
        best, best_pos = "", -1
        for token, adj in _SIZE_OPTIONS:
            p = line.find(token)
            if 0 <= p < check_pos and p > best_pos:
                best, best_pos = adj, p
        if best:
            return best
    return ""


def _detect_shift(text: str) -> int | None:
    value, _ = _value_after(text.splitlines(), "Schichtbetrieb")
    hay = value or text
    m = re.search(r"(\d)\s*-?\s*Schicht", hay)
    if m:
        return int(m.group(1))
    m = re.search(r"(ein|zwei|drei)\s*-?\s*schicht", hay, re.IGNORECASE)
    return _WORD_SHIFT[m.group(1).lower()] if m else None


def parse_fragenkatalog(path: str | Path) -> Fragenkatalog:
    text = _pdftotext(path)
    lines = text.splitlines()
    fk = Fragenkatalog()

    fk.firma = _value_after(lines, "Antragstellendes Unternehmen")[0]
    strasse, addr_idx = _value_after(lines, "Anschrift")
    fk.strasse = strasse
    if addr_idx != -1:
        plz, stadt = _find_plz_city(lines, addr_idx)
        fk.plz, fk.stadt = plz or "", stadt or ""

    massnahme, _ = _value_after(lines, "Standort der Maßnahme falls abweichend")
    if not massnahme:
        massnahme, _ = _value_after(lines, "Standort der Maßnahme")
    if massnahme:
        fk.massnahme_strasse, fk.massnahme_plz, fk.massnahme_stadt = _parse_addr_oneline(massnahme)

    betrieb, _ = _value_after(lines, "Betriebsgesellschaft")
    if betrieb and "abweichend" not in betrieb.lower():
        fk.betreiberfirma = betrieb

    fk.groesse = _detect_size(lines)
    fk.schicht_anzahl = _detect_shift(text)
    fk.art_der_anlage = _value_after(lines, "Art der Anlage bzw. des Prozesses")[0] \
        or _value_after(lines, "Art der Anlage")[0]
    fk.vorhaben = _block_after(lines, "Beschreibung des Vorhabens")

    wz, _ = _value_after(lines, "Wirtschaftszweigklassifikation")
    m = re.search(r"\b(\d{4})\b", wz)
    fk.wz_code = m.group(1) if m else ""

    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    fk.email = m.group(0) if m else None
    return fk


def fragenkatalog_to_input(fk: Fragenkatalog, *, website: str | None = None) -> StandortInput:
    """Build the F3 input.

    The described company is the **Antragstellendes Unternehmen** (the applicant), not the
    Betriebsgesellschaft — the Betriebsgesellschaft field is captured on `fk` for reference
    but is not the subject of the description. Use the CLI `--betreiber` to force the
    Untervermieter phrasing when genuinely needed. Geo prefers the measure location.
    """
    inp = StandortInput(
        firma=fk.firma,
        strasse=fk.massnahme_strasse or fk.strasse,
        plz=fk.massnahme_plz or fk.plz or "",
        stadt=fk.massnahme_stadt or fk.stadt or "",
        website=website,
        groesse=fk.groesse,
    )
    if fk.schicht_anzahl:
        inp.operational = OperationalDefaults.from_shift(fk.schicht_anzahl)
    return inp


def find_fragenkatalog(project_dir: str | Path) -> Path | None:
    project_dir = Path(project_dir)
    hits = sorted(p for p in project_dir.glob("*.pdf") if "fragenkatalog" in p.name.lower())
    return hits[0] if hits else None
