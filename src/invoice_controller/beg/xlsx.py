"""Kostenzusammenstellung writers (B5, rebuilt for Phase 2c V7 on 2026-10-05) — EH and EM
openpyxl layouts mirroring the consultant's ground-truth sheets (examples/BEG):

- **EM** ("Technischer Projektnachweis"): `Gewerk | Firma | Re-Nr. | Re-Datum |
  Re.-Positionen | Re-Betrag | bezahlt | förderfähig | Anmerkung | Förderung`,
  Gewerk-grouped Maßnahmen block → "Summe techn. Maßnahmen" → Baubegleitung block →
  its sum row → Gesamtsumme → legend → metadata footer.
- **EH** ("Bestätigung nach Durchführung"): `Gewerk | Firma | Re-Nr. | Re-Datum |
  Re-Positionen | Re-Betrag | Förderfähiger Betrag | Info`, Maßnahmen rows →
  "Baubegleitung & Fachplanung" → three sum rows → footer.

V7 adds, right of the consultant's columns so A–J stay as they are: `Auftrag erteilt`,
`Datumsprüfung` (the EEW date rules as a formula against the header cells) and
`Status`. A parameter block above the table holds the program dates and the
Fördersatz/Deckel figures as yellow INPUTS; the Förderung formulas reference those
cells instead of carrying literals. The consultant's row colours (green = alles in
Ordnung, yellow = Unterlagen/Infos fehlen, red = nicht förderfähig/prüfen) are
conditional formats driven by the row's Status and the date formula — never fills
written by us. `förderfähig` stays an empty input until B3 (the eligibility rules).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from invoice_controller.beg.compute import BegRow, BegTable
from invoice_controller.beg.funding import BegProgramType
from invoice_controller.normalize import format_de_decimal
from invoice_controller.template.cells import (
    BOLD,
    CF_GREEN,
    CF_RED,
    CF_YELLOW,
    DATE,
    EUR,
    PCT,
    WRAP,
    fill_when,
    formula,
    inp,
    label,
    landscape_fit_to_width,
    red_when,
)

SHEET = "Kostenzusammenstellung"
# Parameter block (rows 2–7); the table's column headers sit in HEADER_ROW.
P = {
    "antragsteller": "B2", "vorgangsnummer": "D2",
    "antragstellung": "B3", "aavm": "D3",
    "bescheid": "B4", "untergrenze": "D4",
    "satz": "B5", "cap": "D5",
    "bb_satz": "B6", "bb_cap": "D6",
}
HEADER_ROW = 9
FIRST_ROW = 10
COLS_EM = ["Gewerk", "Firma", "Re-Nr.", "Re-Datum", "Re.-Positionen", "Re-Betrag",
           "bezahlt", "förderfähig", "Anmerkung", "Förderung", "Auftrag erteilt", "Datumsprüfung", "Status"]
COLS_EH = ["Gewerk", "Firma", "Re-Nr.", "Re-Datum", "Re-Positionen", "Re-Betrag",
           "Förderfähiger Betrag", "Info", "", "", "Auftrag erteilt", "Datumsprüfung", "Status"]
STATUS_LABEL = {"ok": "ok", "offen": "offen", "fehler": "prüfen"}


def _header(ws: Worksheet, table: BegTable, *, em: bool) -> None:
    meta, d = table.meta, table.dates
    title = ("Kostenzusammenstellung - Technischer Projektnachweis" if em
             else "Kostenzusammenstellung - Bestätigung nach Durchführung")
    label(ws, "A1", title)
    label(ws, "A2", "Antragsteller"); inp(ws, P["antragsteller"], meta.antragsteller_name)
    label(ws, "C2", "Vorgangsnummer"); inp(ws, P["vorgangsnummer"], meta.vorgangsnummer)
    label(ws, "A3", "Antragstellung"); inp(ws, P["antragstellung"], d.antragstellung, fmt=DATE)
    label(ws, "C3", "AavM-Genehmigung"); inp(ws, P["aavm"], d.aavm_genehmigung, fmt=DATE)
    label(ws, "A4", "Zuwendungsbescheid datiert"); inp(ws, P["bescheid"], d.bescheid_datiert, fmt=DATE)
    label(ws, "C4", "Untergrenze Rechnungsdatum")
    formula(ws, P["untergrenze"], '=IF(D3<>"",D3,IF(B4<>"",B4,""))', fmt=DATE)
    label(ws, "A5", "Fördersatz Maßnahmen (%)"); inp(ws, P["satz"], meta.foerdersatz_pct)
    label(ws, "C5", "Deckel förderfähige Kosten (€)"); inp(ws, P["cap"], meta.foerderfaehige_kosten_cap, fmt=EUR)
    label(ws, "A6", "Fördersatz Baubegleitung (%)"); inp(ws, P["bb_satz"], meta.baubegleitung_foerdersatz_pct)
    label(ws, "C6", "Deckel Baubegleitung (€)"); inp(ws, P["bb_cap"], meta.baubegleitung_kosten_cap, fmt=EUR)
    if meta.foerdersatz_zusammensetzung:
        label(ws, "E5", f"({meta.foerdersatz_zusammensetzung})", bold=False)
    label(ws, "A7", "↓ Auftrag erteilt ≥ Antragstellung (B3) · Rechnungsdatum ≥ Untergrenze (D4). "
                    "Gelb = Eingabe, Formelzellen ohne Füllung; Zeilenfarbe: grün ok · gelb offen · rot prüfen/nicht förderfähig.",
          bold=False)
    # a missing rule date or Fördersatz is itself a finding
    for ref in ("B3", "B4", "D4", "B5"):
        red_when(ws, ref, f'${ref[0]}${ref[1:]}=""')
    cols = COLS_EM if em else COLS_EH
    for i, text in enumerate(cols, start=1):
        if text:
            c = ws.cell(HEADER_ROW, i, text)
            c.font = BOLD
            c.alignment = WRAP


def _write_row(ws: Worksheet, r: int, row: BegRow, *, gewerk: str | None, em: bool) -> None:
    ws.cell(r, 1, gewerk)
    inp(ws, f"B{r}", row.firma)
    inp(ws, f"C{r}", row.re_nr)
    inp(ws, f"D{r}", row.re_datum, fmt=DATE)
    inp(ws, f"E{r}", row.re_positionen)
    inp(ws, f"F{r}", row.re_betrag, fmt=EUR)
    if em:
        inp(ws, f"G{r}", row.bezahlt, fmt=EUR)
        inp(ws, f"H{r}", row.foerderfaehig, fmt=EUR)        # empty until B3/consultant
        note_col = "I"
    else:
        inp(ws, f"G{r}", row.foerderfaehig, fmt=EUR)        # EH: Förderfähiger Betrag
        note_col = "H"
    note = row.anmerkung_text
    if row.flags:
        note = "⚠ " + "; ".join(row.flags) + ("; " + note if note else "")
    ws.cell(r, ord(note_col) - 64, note or None).alignment = WRAP

    inp(ws, f"K{r}", row.auftrag_datum, fmt=DATE)
    formula(ws, f"L{r}",
            f'=IF(AND(K{r}<>"",$B$3<>"",K{r}<$B$3),"NEIN!",IF(AND($D$4<>"",D{r}<$D$4),"NEIN!","ja"))')
    inp(ws, f"M{r}", STATUS_LABEL.get(row.status, row.status))

    # verdict colours: the offending date cell red; the whole row by status / formula
    red_when(ws, f"K{r}", f'AND(K{r}<>"",$B$3<>"",K{r}<$B$3)')
    red_when(ws, f"D{r}", f'AND(D{r}<>"",$D$4<>"",D{r}<$D$4)')
    fill_when(ws, f"A{r}:M{r}", f'OR($L{r}="NEIN!",$M{r}="prüfen")', CF_RED)
    fill_when(ws, f"A{r}:M{r}", f'AND($L{r}<>"NEIN!",$M{r}="offen")', CF_YELLOW)
    fill_when(ws, f"A{r}:M{r}", f'AND($L{r}<>"NEIN!",$M{r}="ok")', CF_GREEN)


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
    return format_de_decimal(value).removesuffix(",00")


def _foerderung_text(satz: Decimal | None, cap: Decimal | None, *, bis: bool) -> str | None:
    if satz is None:
        return None
    if cap is None:
        return f"Förderung {_de_short(satz)}%"
    joiner = "bis" if bis else "auf"
    return f"Förderung {_de_short(satz)}% {joiner} {_de_short(cap)}€"


def _missing(ws: Worksheet, row: int, col: int, value, fmt: str | None = None):
    """Footer metadata cell: the value, or a red 'fehlt' — never a silent blank."""
    c = ws.cell(row, col)
    if value is None:
        c.value = "fehlt"
        c.fill = CF_RED
    else:
        c.value = value
        if fmt:
            c.number_format = fmt
    return c


def write_kostenzusammenstellung(table: BegTable, output_path: Path) -> None:
    em = table.meta.program_type is not BegProgramType.EH
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    meta = table.meta
    _header(ws, table, em=em)

    r = FIRST_ROW
    r, mass_rows = _gewerk_blocks(table.massnahmen, ws, r, em=em)
    r += 1

    if em:
        # Summe techn. Maßnahmen + Förderung = MIN(Σ förderfähig, Deckel) × Satz — the
        # Satz/Deckel come from the parameter cells, so a corrected input recomputes.
        sum_r = r
        label(ws, f"A{sum_r}", "Summe techn. Maßnahmen")
        for col_letter in ("F", "G", "H"):
            f = _sum_formula(col_letter, mass_rows)
            if f:
                formula(ws, f"{col_letter}{sum_r}", f, fmt=EUR, bold=True)
        foerderung_label = _foerderung_text(meta.foerdersatz_pct, meta.foerderfaehige_kosten_cap, bis=False)
        if foerderung_label and meta.foerdersatz_zusammensetzung:
            foerderung_label += f" ({meta.foerdersatz_zusammensetzung})"
        ws.cell(sum_r, 9, foerderung_label if meta.foerdersatz_pct is not None else "Fördersatz fehlt")
        if mass_rows:
            formula(ws, f"J{sum_r}",
                    f'=IF({P["satz"]}="","",IF({P["cap"]}="",H{sum_r},MIN(H{sum_r},{P["cap"]}))*{P["satz"]}/100)',
                    fmt=EUR, bold=True)
        r = sum_r + 2

        r, bb_rows = _gewerk_blocks(table.baubegleitung, ws, r, em=em)
        r += 1
        bb_sum_r = r
        label(ws, f"A{bb_sum_r}", "Summe Baubegleitung")
        for col_letter in ("F", "G", "H"):
            f = _sum_formula(col_letter, bb_rows)
            if f:
                formula(ws, f"{col_letter}{bb_sum_r}", f, fmt=EUR, bold=True)
        ws.cell(bb_sum_r, 9, _foerderung_text(meta.baubegleitung_foerdersatz_pct, meta.baubegleitung_kosten_cap, bis=True))
        if bb_rows:
            formula(ws, f"J{bb_sum_r}",
                    f'=IF({P["bb_satz"]}="","",IF({P["bb_cap"]}="",H{bb_sum_r}*{P["bb_satz"]}/100,'
                    f'MIN(H{bb_sum_r}*{P["bb_satz"]}/100,{P["bb_cap"]})))',
                    fmt=EUR, bold=True)
        r = bb_sum_r + 2

        total_r = r
        label(ws, f"A{total_r}", "Gesamtsumme")
        ws.cell(total_r, 2, "Summe / gesamt")
        for col_letter in ("F", "G", "H"):
            formula(ws, f"{col_letter}{total_r}", f"={col_letter}{sum_r}+{col_letter}{bb_sum_r}", fmt=EUR, bold=True)
        # a missing Fördersatz leaves its Förderung cell "" (red parameter cell says why);
        # N() keeps the total a number instead of #VALUE!
        formula(ws, f"J{total_r}", f"=N(J{sum_r})+N(J{bb_sum_r})", fmt=EUR, bold=True)
        r = total_r + 2
    else:
        label(ws, f"A{r}", "Baubegleitung & Fachplanung")
        r += 2
        r, bb_rows = _gewerk_blocks(table.baubegleitung, ws, r, em=em)
        r += 1
        sum_r = r
        label(ws, f"A{sum_r}", "Summe Maßnahmen")
        for col_letter in ("F", "G"):
            f = _sum_formula(col_letter, mass_rows)
            if f:
                formula(ws, f"{col_letter}{sum_r}", f, fmt=EUR, bold=True)
        label(ws, f"A{sum_r + 1}", "Summe Baubegleitung & Fachplanung")
        for col_letter in ("F", "G"):
            f = _sum_formula(col_letter, bb_rows)
            if f:
                formula(ws, f"{col_letter}{sum_r + 1}", f, fmt=EUR, bold=True)
        label(ws, f"A{sum_r + 2}", "Gesamtsumme")
        for col_letter in ("F", "G"):
            formula(ws, f"{col_letter}{sum_r + 2}", f"={col_letter}{sum_r}+{col_letter}{sum_r + 1}", fmt=EUR, bold=True)
        r = sum_r + 4

    # Legend (the consultant's convention, rendered by the row conditional formats).
    ws.cell(r, 3, "alles in Ordnung").fill = CF_GREEN
    ws.cell(r, 6, "Offene Fragen:")
    ws.cell(r + 1, 3, "Unterlagen / Infos fehlen").fill = CF_YELLOW
    ws.cell(r + 2, 3, "nicht förderfähig / prüfen").fill = CF_RED
    r += 4

    # Metadata footer — every missing value is a loud red "fehlt".
    if em:
        footer = [
            ("Vorgangsnummer:", meta.vorgangsnummer, None),
            ("Antrag gestellt:", table.dates.antragstellung, DATE),
            ("Zuwendungsbescheid:", table.dates.bescheid_datiert, DATE),
            ("Kosten Maßnahmen:", float(meta.geplante_kosten_massnahmen) if meta.geplante_kosten_massnahmen is not None else None, EUR),
            ("Kosten Baubegleitung:", float(meta.geplante_kosten_baubegleitung) if meta.geplante_kosten_baubegleitung is not None else None, EUR),
        ]
    else:
        footer = [
            ("Wohneinheiten:", meta.wohneinheiten, None),
            ("BzA erstellt:", meta.antrag_date, DATE),
            ("Standard:", meta.eh_standard, None),
            ("Antragstellung:", table.dates.antragstellung, DATE),
            ("Zuwendungsbescheid:", table.dates.bescheid_datiert, DATE),
        ]
    for text, value, fmt in footer:
        ws.cell(r, 2, text)
        _missing(ws, r, 3, value, fmt)
        r += 1
    ws.cell(r, 2, "Leistungszeiträume:")
    if table.leistungszeitraum:
        lo, hi = table.leistungszeitraum
        ws.cell(r, 3, f"{lo.strftime('%d.%m.%Y')}-{hi.strftime('%d.%m.%Y')} (lt. Rechnungsdaten)")
    else:
        ws.cell(r, 3, "fehlt").fill = CF_RED

    widths = {"A": 26, "B": 30, "C": 28, "D": 14, "E": 14, "F": 13, "G": 13, "H": 13, "I": 44, "J": 13,
              "K": 13, "L": 12, "M": 8}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = f"A{FIRST_ROW}"
    landscape_fit_to_width(ws, header_row=HEADER_ROW, a3=True)
    wb.save(output_path)


__all__ = ["write_kostenzusammenstellung", "HEADER_ROW", "FIRST_ROW", "P", "date"]
