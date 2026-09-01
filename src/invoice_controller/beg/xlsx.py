"""Kostenzusammenstellung writers (B5) — EH and EM openpyxl layouts.

Mirrors the consultant's ground-truth sheets (EM_Madroch, EH_Fissenewert):

- **EM** ("Technischer Projektnachweis"): `Gewerk | Firma | Re-Nr. | Re-Datum |
  Re.-Positionen | Re-Betrag | bezahlt | förderfähig | Anmerkung | Förderung`,
  Gewerk-grouped Maßnahmen block → "Summe techn. Maßnahmen" (with the Förderung
  formula) → Baubegleitung block → its sum row → Gesamtsumme → legend → metadata
  footer (Vorgangsnummer, dates, geplante Kosten, Leistungszeiträume).
- **EH** ("Bestätigung nach Durchführung"): `Gewerk | Firma | Re-Nr. | Re-Datum |
  Re-Positionen | Re-Betrag | Förderfähiger Betrag | Info`, Maßnahmen rows →
  "Baubegleitung & Fachplanung" section → three sum rows → Unterlagen checklist +
  footer (Wohneinheiten, BzA erstellt, Standard).

Same rules as every writer in this project: live SUM/MIN formulas (Excel and
LibreOffice recalculate .xlsx on open — no cached-value machinery), red fill +
message for every review flag, judgment cells (förderfähig) left EMPTY so the
Förderung formulas react when the consultant fills them. Program parameters that
FundingMeta could not extract render red "fehlt" — never guessed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from invoice_controller.beg.compute import BegRow, BegTable
from invoice_controller.beg.funding import BegProgramType
from invoice_controller.normalize import format_de_decimal

_EUR = "#,##0.00"
_DATE = "DD.MM.YYYY"
_RED_FILL = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
_YELLOW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
_GREEN_FILL = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
_BOLD = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")


def _eur(ws: Worksheet, row: int, col: int, value: Decimal | None):
    c = ws.cell(row, col)
    if value is not None:
        c.value = float(value)
        c.number_format = _EUR
    return c


def _date_cell(ws: Worksheet, row: int, col: int, value: date | None):
    c = ws.cell(row, col, value)
    if value is not None:
        c.number_format = _DATE
    return c


def _missing(ws: Worksheet, row: int, col: int, value, fmt: str | None = None):
    """Footer metadata cell: the value, or a red 'fehlt' — never a silent blank."""
    c = ws.cell(row, col)
    if value is None:
        c.value = "fehlt"
        c.fill = _RED_FILL
    else:
        c.value = value
        if fmt:
            c.number_format = fmt
    return c


def _write_row(ws: Worksheet, r: int, row: BegRow, *, gewerk: str | None, em: bool) -> None:
    ws.cell(r, 1, gewerk)
    ws.cell(r, 2, row.firma)
    ws.cell(r, 3, row.re_nr)
    _date_cell(ws, r, 4, row.re_datum)
    ws.cell(r, 5, row.re_positionen)
    _eur(ws, r, 6, row.re_betrag)
    if em:
        _eur(ws, r, 7, row.bezahlt)
        _eur(ws, r, 8, row.foerderfaehig)          # empty until B3/consultant
        note_col = 9
    else:
        _eur(ws, r, 7, row.foerderfaehig)          # EH: Förderfähiger Betrag
        note_col = 8

    note = row.anmerkung_text
    if row.flags:
        note = "⚠ " + "; ".join(row.flags) + ("; " + note if note else "")
    c = ws.cell(r, note_col, note or None)
    c.alignment = _WRAP
    if row.flags:
        c.fill = _RED_FILL
    elif "förderfähig prüfen" in note:
        c.fill = _YELLOW_FILL


def _gewerk_blocks(rows: list[BegRow], ws: Worksheet, start: int, *, em: bool) -> tuple[int, list[int]]:
    """Write Gewerk-grouped rows (label on the group's first row, blank row between
    groups). Returns (next free row, data row numbers)."""
    r = start
    data_rows: list[int] = []
    last_gewerk: str | None = None
    for row in rows:
        if last_gewerk is not None and row.gewerk != last_gewerk:
            r += 1
        _write_row(ws, r, row, gewerk=row.gewerk if row.gewerk != last_gewerk else None, em=em)
        data_rows.append(r)
        last_gewerk = row.gewerk
        r += 1
    return r, data_rows


def _sum_formula(col_letter: str, data_rows: list[int]) -> str | None:
    if not data_rows:
        return None
    return f"=SUM({col_letter}{data_rows[0]}:{col_letter}{data_rows[-1]})"


def _de_short(value: Decimal) -> str:
    """German number with the consultant's Förderung-text habit: '20%' not '20,00 %'."""
    text = format_de_decimal(value)
    return text.removesuffix(",00")


def _foerderung_text(satz: Decimal | None, cap: Decimal | None, *, bis: bool) -> str | None:
    if satz is None:
        return None
    if cap is None:
        return f"Förderung {_de_short(satz)}%"
    joiner = "bis" if bis else "auf"
    return f"Förderung {_de_short(satz)}% {joiner} {_de_short(cap)}€"


def write_kostenzusammenstellung(table: BegTable, output_path: Path) -> None:
    em = table.meta.program_type is not BegProgramType.EH
    wb = Workbook()
    ws = wb.active
    ws.title = "Kostenzusammenstellung"
    meta = table.meta

    title = (
        "Kostenzusammenstellung - Technischer Projektnachweis"
        if em
        else "Kostenzusammenstellung - Bestätigung nach Durchführung"
    )
    ws.cell(1, 1, title).font = _BOLD

    headers = (
        ["Gewerk", "Firma", "Re-Nr.", "Re-Datum", "Re.-Positionen", "Re-Betrag",
         "bezahlt", "förderfähig", "Anmerkung", "Förderung"]
        if em
        else ["Gewerk", "Firma", "Re-Nr.", "Re-Datum", "Re-Positionen", "Re-Betrag",
              "Förderfähiger Betrag", "Info"]
    )
    for col, text in enumerate(headers, start=1):
        ws.cell(2, col, text).font = _BOLD
    if em:
        ws.cell(3, 7, "Skonto/Nachl.")

    r = 5
    r, mass_rows = _gewerk_blocks(table.massnahmen, ws, r, em=em)
    r += 1

    if em:
        # Summe techn. Maßnahmen + Förderung formula (=MIN(Σ förderfähig, cap) × Satz).
        sum_r = r
        ws.cell(sum_r, 1, "Summe techn. Maßnahmen").font = _BOLD
        for col_letter, col in (("F", 6), ("G", 7), ("H", 8)):
            f = _sum_formula(col_letter, mass_rows)
            if f:
                ws.cell(sum_r, col, f).number_format = _EUR
        foerderung_label = _foerderung_text(meta.foerdersatz_pct, meta.foerderfaehige_kosten_cap, bis=False)
        if foerderung_label and meta.foerdersatz_zusammensetzung:
            foerderung_label += f" ({meta.foerdersatz_zusammensetzung})"
        ws.cell(sum_r, 9, foerderung_label)
        if meta.foerdersatz_pct is not None and mass_rows:
            cap = meta.foerderfaehige_kosten_cap
            h_ref = f"H{sum_r}"
            base = f"MIN({h_ref},{cap})" if cap is not None else h_ref
            ws.cell(sum_r, 10, f"={base}*{meta.foerdersatz_pct / Decimal(100)}").number_format = _EUR
        elif meta.foerdersatz_pct is None:
            ws.cell(sum_r, 10, "Fördersatz fehlt").fill = _RED_FILL
        r = sum_r + 2

        r, bb_rows = _gewerk_blocks(table.baubegleitung, ws, r, em=em)
        r += 1
        bb_sum_r = r
        for col_letter, col in (("F", 6), ("G", 7), ("H", 8)):
            f = _sum_formula(col_letter, bb_rows)
            if f:
                ws.cell(bb_sum_r, col, f).number_format = _EUR
        ws.cell(bb_sum_r, 9, _foerderung_text(
            meta.baubegleitung_foerdersatz_pct, meta.baubegleitung_kosten_cap, bis=True
        ))
        if meta.baubegleitung_foerdersatz_pct is not None and bb_rows:
            satz = meta.baubegleitung_foerdersatz_pct / Decimal(100)
            cap = meta.baubegleitung_kosten_cap
            expr = f"H{bb_sum_r}*{satz}"
            if cap is not None:
                expr = f"MIN({expr},{cap})"
            ws.cell(bb_sum_r, 10, f"={expr}").number_format = _EUR
        r = bb_sum_r + 2

        total_r = r
        ws.cell(total_r, 1, "Gesamtsumme").font = _BOLD
        ws.cell(total_r, 2, "Summe / gesamt")
        for col_letter, col in (("F", 6), ("G", 7), ("H", 8), ("J", 10)):
            ws.cell(total_r, col, f"={col_letter}{sum_r}+{col_letter}{bb_sum_r}").number_format = _EUR
        r = total_r + 2
    else:
        ws.cell(r, 1, "Baubegleitung & Fachplanung").font = _BOLD
        r += 2
        r, bb_rows = _gewerk_blocks(table.baubegleitung, ws, r, em=em)
        r += 1
        sum_r = r
        ws.cell(sum_r, 1, "Summe Maßnahmen").font = _BOLD
        for col_letter, col in (("F", 6), ("G", 7)):
            f = _sum_formula(col_letter, mass_rows)
            if f:
                ws.cell(sum_r, col, f).number_format = _EUR
        ws.cell(sum_r + 1, 1, "Summe Baubegleitung & Fachplanung").font = _BOLD
        for col_letter, col in (("F", 6), ("G", 7)):
            f = _sum_formula(col_letter, bb_rows)
            if f:
                ws.cell(sum_r + 1, col, f).number_format = _EUR
        ws.cell(sum_r + 2, 1, "Gesamtsumme").font = _BOLD
        for col_letter, col in (("F", 6), ("G", 7)):
            ws.cell(sum_r + 2, col, f"={col_letter}{sum_r}+{col_letter}{sum_r + 1}").number_format = _EUR
        r = sum_r + 4

    # Legend (colors mirror the consultant's convention).
    ws.cell(r, 3, "alles in Ordnung").fill = _GREEN_FILL
    ws.cell(r, 6, "Offene Fragen:")
    ws.cell(r + 1, 3, "Unterlagen / Infos fehlen").fill = _YELLOW_FILL
    ws.cell(r + 2, 3, "nicht förderfähig / prüfen").fill = _RED_FILL
    r += 4

    # Metadata footer — every missing value is a loud red "fehlt".
    if em:
        footer = [
            ("Vorgangsnummer:", meta.vorgangsnummer, None),
            ("Antrag gestellt:", meta.antrag_date, _DATE),
            ("Zuwendungsbescheid:", meta.bescheid_date, _DATE),
            ("Kosten Maßnahmen:", float(meta.geplante_kosten_massnahmen) if meta.geplante_kosten_massnahmen is not None else None, _EUR),
            ("Kosten Baubegleitung:", float(meta.geplante_kosten_baubegleitung) if meta.geplante_kosten_baubegleitung is not None else None, _EUR),
        ]
    else:
        footer = [
            ("Wohneinheiten:", meta.wohneinheiten, None),
            ("BzA erstellt:", meta.antrag_date, _DATE),
            ("Standard:", meta.eh_standard, None),
            ("Antragstellung:", meta.antrag_date, _DATE),
            ("Zuwendungsbescheid:", meta.bescheid_date, _DATE),
        ]
    for label, value, fmt in footer:
        ws.cell(r, 2, label)
        _missing(ws, r, 3, value, fmt)
        r += 1
    ws.cell(r, 2, "Leistungszeiträume:")
    if table.leistungszeitraum:
        lo, hi = table.leistungszeitraum
        ws.cell(r, 3, f"{lo.strftime('%d.%m.%Y')}-{hi.strftime('%d.%m.%Y')} (lt. Rechnungsdaten)")
    else:
        ws.cell(r, 3, "fehlt").fill = _RED_FILL

    widths = {"A": 24, "B": 30, "C": 14, "D": 12, "E": 14, "F": 12, "G": 12, "H": 12, "I": 40, "J": 12}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    wb.save(output_path)
