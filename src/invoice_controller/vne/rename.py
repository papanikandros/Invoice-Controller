"""Phase 2c V6 — rename the uploaded invoices (consultant request 2026-09-24, format
decided 2026-10-03/04): `<Rechnungsgeber>_<Rechnungsnummer>_<YYYY-MM-DD>.pdf`, ALWAYS,
scans included. The new name is what the VNE-Maske's Beleg column shows and what
the colleague downloads as a ZIP next to the workbook.

A field that failed its checks is written as `UNKLAR` — a wrong-looking name must never
look right (the "020 / 01.01.2020" Sönnecken scan of EK4_333 run 5). Originals are
never touched; the renamed copies live in `<run>/renamed/` with an `Umbenennung.csv`
(old → new, reason) so a wrong name is caught in seconds.
"""

from __future__ import annotations

import csv
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from invoice_controller.match.vendor import _LEGAL_FORMS
from invoice_controller.models import InvoiceDocument

UNKLAR = "UNKLAR"
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_MAX_PART = 60


def _clean(part: str) -> str:
    part = _FORBIDDEN.sub("-", part)
    part = _SPACES.sub(" ", part).strip(" .-_")
    return part[:_MAX_PART].rstrip(" .-_") or UNKLAR


def vendor_part(vendor: str | None) -> str:
    if not vendor or not vendor.strip():
        return UNKLAR
    stripped = _LEGAL_FORMS.sub(" ", vendor)
    stripped = re.sub(r"(?<!\w)(\w)\s*&\s*(\w)(?!\w)", r"\1&\2", stripped)   # initials only: "L & R" → "L&R"
    stripped = re.sub(r"\s*&[\s.]*$", " ", stripped)                   # dangling "&" from "GmbH & Co.KG"
    stripped = re.sub(r"\s*&\s*\.", " ", stripped)                     # "& ." residue mid-name
    return _clean(stripped)


def number_part(number: str | None) -> str:
    if not number or not number.strip():
        return UNKLAR
    return _clean("".join(number.split()))


def date_part(d: date | None, *, today: date | None = None) -> str:
    """ISO so a folder sorts chronologically. A scan-garbage date (01.01.2020 from a
    '020' misread) is as wrong as a missing one: no close-out handles invoices older
    than five years or dated in the future."""
    today = today or date.today()
    if d is None or d < date(today.year - 5, today.month, 1) or d > date(today.year + 1, 12, 31):
        return UNKLAR
    return d.isoformat()


def target_name(inv: InvoiceDocument, *, unusable: bool = False) -> str:
    """The new filename; `unusable` (netto 0 / duplicate) forces UNKLAR on every
    field the extraction could not vouch for."""
    if unusable and inv.netto == 0:
        return f"{UNKLAR}_{UNKLAR}_{UNKLAR}{inv.source_path.suffix.lower() or '.pdf'}"
    return (f"{vendor_part(inv.vendor_name)}_{number_part(inv.invoice_number)}_{date_part(inv.invoice_date)}"
            f"{inv.source_path.suffix.lower() or '.pdf'}")


@dataclass
class Renamed:
    original: Path
    new_name: str
    note: str = ""          # why a field is UNKLAR / why a suffix was added


def plan_renames(invoices: list[InvoiceDocument], unusable: set[Path] | None = None) -> list[Renamed]:
    """Pure: decide every new name, resolve collisions with `_2`, `_3` … (two scans of
    one invoice land on the same name — the duplicate flag says which to delete)."""
    unusable = unusable or set()
    taken: dict[str, int] = {}
    out: list[Renamed] = []
    for inv in invoices:
        name = target_name(inv, unusable=inv.source_path in unusable)
        notes = []
        if UNKLAR in name:
            notes.append("Feld nicht lesbar → UNKLAR, Dateiname von Hand prüfen")
        if inv.vendor_from_filename:
            notes.append("Rechnungssteller aus dem alten Dateinamen (Briefkopf nicht lesbar)")
        key = name.lower()
        if key in taken:
            taken[key] += 1
            stem, suffix = name.rsplit(".", 1)
            name = f"{stem}_{taken[key]}.{suffix}"
            notes.append("gleicher Name wie eine andere Datei — Duplikat?")
        else:
            taken[key] = 1
        out.append(Renamed(original=inv.source_path, new_name=name, note="; ".join(notes)))
    return out


def write_renamed(renames: list[Renamed], out_dir: Path) -> Path:
    """Copy the originals under their new names into `out_dir`, write Umbenennung.csv,
    return the ZIP path (the download the colleague gets)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in renames:
        shutil.copy2(r.original, out_dir / r.new_name)
    with (out_dir / "Umbenennung.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["alter Name", "neuer Name", "Hinweis"])
        for r in renames:
            w.writerow([r.original.name, r.new_name, r.note])
    zip_path = out_dir.parent / "Rechnungen_umbenannt.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in renames:
            z.write(out_dir / r.new_name, r.new_name)
        z.write(out_dir / "Umbenennung.csv", "Umbenennung.csv")
    return zip_path
