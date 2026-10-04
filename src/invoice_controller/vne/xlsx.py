"""Render a VneResult into the VNE-Tabelle .xlsx — the consultant's `(Vorlage VNE-Maske)`
cell logic, rebuilt 2026-10-04 (Phase 2c V2/V3/V4) from the ten ground-truth workbooks:

* 3-row invoice blocks: row 1 `A Rechnungssteller | B Beleg | C Auftrag erteilt | D Zahlungsdatum
  | E Rechnungsdatum | F check | G Brutto | H Netto (formula) | I Anmerkung`; row 2 the
  "… zu früh!" message formulas, Skonto, `J` Netto nach Skonto, first category `K/L/M/N/O`;
  row 3 the second category.
* ONLY extracted or typed values are literals (yellow `FFFFFF99`, as in every consultant
  sheet); everything derived is an Excel formula the reviewer can follow: H = G/(1+MwSt),
  J = IF(F="ja", H − H·Skonto, 0), M/N/O = IF(K="IK", J·L, 0), Σ rows, Förderbetrag chain,
  the Fristen in the header.
* The two binding date rules (consultant, 2026-10-04) live in THREE places that must agree:
  the F formula (+ message row), the Python flags (`vne/compute.py`), and a conditional
  format that turns the offending DATE CELL red. `Auftrag erteilt ≥ Antragstellung` (B4);
  `Rechnungsdatum ≥ B8`, where B8 = AavM-Genehmigung (E4) if present, else Zuwendungsbescheid
  datiert (B6). An unknown date never fails a check (nobody can validate what no document
  states) — but a missing header date turns ITS cell red.
* Red is NEVER a fill written by us; it is conditional formatting, so a corrected input
  clears it. Two helper inputs sit right of the template columns: W MwSt-Satz, X Netto lt.
  Rechnung (the extracted figure H is checked against).

Number formats are copied from the consultant sheets (Anteil displays "97,12 %"). The
Positionsabgleich sheet (Phase 2c) follows on a second sheet; the Maske stays identical in
layout to the consultant's template columns A–V.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import openpyxl
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from invoice_controller.config import ProjektConfig
from invoice_controller.models import InvoiceType
from invoice_controller.normalize import format_de_decimal
from invoice_controller.vne.abgleich import AbgleichResult
from invoice_controller.vne.compute import VneResult, VneRow

SHEET_NAME = "(Vorlage VNE-Maske)"
ABGLEICH_SHEET_NAME = "Positionsabgleich"

# Number formats copied from the consultant's own VNE sheets (examples/Craemer).
_EUR = "#,##0.00\\ [$€-407];[RED]\\-#,##0.00\\ [$€-407]"
_DATE = "DD.MM.YYYY"
_PCT = "0.00\\\xa0%"
_PCT_DISPLAY = "0.0%"
_INPUT = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")   # the template's input yellow
_RED_FILL = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")  # Positionsabgleich sheet
_YELLOW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
_CF_RED = PatternFill(start_color="FF9999", end_color="FF9999", fill_type="solid", bgColor="FF9999")
_BOLD = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")

_TYPE_LABEL = {
    InvoiceType.RECHNUNG: "Rechnung",
    InvoiceType.ANZAHLUNGSRECHNUNG: "Anzahlungsrechnung",
    InvoiceType.TEILRECHNUNG: "Teil-/Abschlagsrechnung",
    InvoiceType.SCHLUSSRECHNUNG: "Schlussrechnung",
    InvoiceType.GUTSCHRIFT: "Gutschrift",
}

# Column letters of the Maske (template A–V) plus our two helper inputs.
COL = {
    "vendor": "A", "beleg": "B", "auftrag": "C", "zahlung": "D", "rechnung": "E", "check": "F",
    "brutto": "G", "netto": "H", "anmerkung": "I", "netto_skonto": "J", "kat": "K", "anteil": "L",
    "ik": "M", "nk": "N", "ek": "O", "beantragt_ik": "Q", "beantragt_nk": "R", "beantragt_ek": "S",
    "ansetzbar_ik": "T", "ansetzbar_nk": "U", "ansetzbar_ek": "V", "mwst": "W", "netto_extracted": "X",
}
_HEADERS = {
    "A": "Herstellername des modulspezifischen Fördergegenstands",
    "B": "Ausgabenbelege (Rechnungsnummer, Rechnungsposition)",
    "C": "Auftrag erteilt, Kaufvertrag",
    "D": "Zahlungsdatum",
    "E": "Rechnungsdatum",
    "F": "Bewilligungs-zeitraum eingehalten?",
    "G": "Betrag\nBrutto",
    "H": "Betrag\nNetto",
    "I": "Anmerkung Bezug",
    "J": "Betrag Netto m. Skto./Rab.",
    "K": "IK oder\nNK oder\nEK",
    "L": "Anteil IK bzw. NK bzw. EK",
    "M": "Betrag Netto\nInvestitions-kosten",
    "N": "Betrag Netto\nNebenkosten",
    "O": "Betrag Netto\nEinsparkonzept",
    "Q": "beantragt\nIK",
    "R": "beantragt\nNK",
    "S": "beantragt\nEK",
    "T": "ansetzbar\nIK",
    "U": "ansetzbar\nNK",
    "V": "ansetzbar\nEK",
    "W": "MwSt-Satz\nlt. Rechnung",
    "X": "Netto\nlt. Rechnung",
}
HEADER_ROW = 10
FIRST_BLOCK_ROW = 11


def _money(ws: Worksheet, row: int, col: int, value: Decimal | None, *, bold: bool = False) -> None:
    """Literal money cell (Positionsabgleich sheet — a report, not the Maske)."""
    if value is None:
        return
    c = ws.cell(row, col, float(value))
    c.number_format = _EUR
    if bold:
        c.font = _BOLD


def _inp(ws: Worksheet, ref: str, value, *, fmt: str | None = None):
    """An INPUT cell: an extracted/typed value, yellow like in every consultant sheet.
    None leaves the cell empty but still yellow (the reviewer sees what is missing)."""
    c = ws[ref]
    if value is not None:
        c.value = float(value) if isinstance(value, Decimal) else value
    c.fill = _INPUT
    if fmt:
        c.number_format = fmt
    return c


def _formula(ws: Worksheet, ref: str, formula: str, *, fmt: str | None = None, bold: bool = False):
    c = ws[ref]
    c.value = formula
    if fmt:
        c.number_format = fmt
    if bold:
        c.font = _BOLD
    return c


def _label(ws: Worksheet, ref: str, text: str, *, bold: bool = True, wrap: bool = False):
    c = ws[ref]
    c.value = text
    if bold:
        c.font = _BOLD
    if wrap:
        c.alignment = _WRAP
    return c


def _red_when(ws: Worksheet, cell_range: str, formula: str) -> None:
    ws.conditional_formatting.add(cell_range, FormulaRule(formula=[formula], fill=_CF_RED, stopIfTrue=False))


def _header(ws: Worksheet, config: ProjektConfig) -> None:
    client = config.client
    _label(ws, "A1", "Antragsteller")
    _inp(ws, "B1", (f"{client.name}" + (f", {client.address}" if client.address else "")) if client else None)
    _label(ws, "A2", "Standort")
    _inp(ws, "B2", None)
    _label(ws, "I1", "Kennung:"); _inp(ws, "J1", config.kennung)
    _label(ws, "I2", "Passwort:"); _inp(ws, "J2", config.passwort)
    _label(ws, "I3", "IBAN:"); _inp(ws, "J3", config.iban)
    _label(ws, "I4", "Steuernummer:"); _inp(ws, "J4", config.steuernummer)

    _label(ws, "A4", "Antragstellung")
    _inp(ws, "B4", config.antragstellung, fmt=_DATE)
    _label(ws, "C4", "AavM", bold=False)
    _inp(ws, "D4", None, fmt=_DATE)                       # Antrag auf vorzeitigen Maßnahmenbeginn (date of the request)
    _inp(ws, "E4", config.aavm_genehmigung, fmt=_DATE)
    _label(ws, "F4", "Genehmigung Antrag auf vorzeitigen Maßnahmenbeginn", bold=False)
    _label(ws, "A5", "Zuwendungsbescheid bei Energiekonzept eingegangen")
    _inp(ws, "B5", config.bescheid_eingegangen, fmt=_DATE)
    _label(ws, "H5", "Fristende Änderungsanzeige (+1 Monat +2 Werktage) →", bold=False)
    _formula(ws, "I5", '=IF(B5="","",WORKDAY(EDATE(B5,1),2))', fmt=_DATE)
    _label(ws, "A6", "Zuwendungsbescheid datiert")
    _inp(ws, "B6", config.bescheid_datiert, fmt=_DATE)
    _label(ws, "C6", "Förderbetrag", bold=False)
    _inp(ws, "D6", config.bescheid.foerderbetrag if config.bescheid else None, fmt=_EUR)
    _label(ws, "H6", "Fristende Einreichung VNE (Ende BWZR + 3 Monate) →", bold=False)
    _formula(ws, "I6", '=IF(D7="","",EDATE(D7,3))', fmt=_DATE)
    _label(ws, "A7", "Bewilligungszeitraum")
    _inp(ws, "B7", config.bewilligungszeitraum_start, fmt=_DATE)
    _label(ws, "C7", "bis", bold=False)
    _inp(ws, "D7", config.bewilligungszeitraum_end, fmt=_DATE)
    _label(ws, "A8", "Untergrenze Rechnungsdatum (AavM-Genehmigung, sonst Bescheid datiert)")
    _formula(ws, "B8", '=IF(E4<>"",E4,IF(B6<>"",B6,""))', fmt=_DATE)
    _label(ws, "A9", "↓ Prüfung je Rechnung: Auftrag erteilt ≥ Antragstellung (B4) · Rechnungsdatum ≥ Untergrenze (B8). "
                     "Rote Datumszellen = Rechnung nicht förderfähig. Gelb = Eingabe aus den Dokumenten, "
                     "alles andere ist Formel.", bold=False, wrap=False)
    # Missing rule dates are themselves a finding: the check silently passes without them.
    _red_when(ws, "B4", '$B$4=""')
    _red_when(ws, "B6", '$B$6=""')
    _red_when(ws, "B8", '$B$8=""')

    for col, text in _HEADERS.items():
        c = ws[f"{col}{HEADER_ROW}"]
        c.value = text
        c.font = _BOLD
        c.alignment = _WRAP


def _beleg_text(row: VneRow) -> str:
    inv = row.invoice
    parts = [row.beleg_name or inv.source_path.name, _TYPE_LABEL[inv.invoice_type]]
    if inv.subject:
        parts.append(inv.subject)
    return "; ".join(parts)


def _block(ws: Worksheet, r: int, row: VneRow, result: VneResult) -> int:
    """Write one invoice as the template's 3-row block starting at row `r`; returns the
    next free row."""
    inv = row.invoice
    r1, r2, r3 = r, r + 1, r + 2
    _inp(ws, f"A{r1}", inv.vendor_name)
    _inp(ws, f"B{r1}", _beleg_text(row)).alignment = _WRAP
    _inp(ws, f"C{r1}", inv.order_date, fmt=_DATE)
    _inp(ws, f"D{r1}", None, fmt=_DATE)                                   # Zahlungsdatum: V5
    _inp(ws, f"E{r1}", inv.invoice_date, fmt=_DATE)
    _formula(ws, f"F{r1}",
             f'=IF(AND(C{r1}<>"",$B$4<>"",C{r1}<$B$4),"NEIN!",IF(AND($B$8<>"",E{r1}<$B$8),"NEIN!","ja"))')
    _inp(ws, f"G{r1}", inv.brutto, fmt=_EUR)
    _formula(ws, f"H{r1}", f'=IF(G{r1}="",X{r1},ROUND(G{r1}/(1+W{r1}),2))', fmt=_EUR)
    anmerkung = " | ".join(f"⚠ {f}" for f in row.flags) if row.flags else (inv.subject or "")
    _inp(ws, f"I{r1}", anmerkung).alignment = _WRAP
    mwst = (inv.mwst_pct / 100) if inv.mwst_pct is not None else (Decimal("0.19") if inv.brutto is not None else None)
    _inp(ws, f"W{r1}", mwst, fmt=_PCT)
    _inp(ws, f"X{r1}", inv.netto, fmt=_EUR)

    _formula(ws, f"C{r2}", f'=IF(AND(C{r1}<>"",$B$4<>"",C{r1}<$B$4),"Auftrag zu früh!","")')
    _formula(ws, f"E{r2}", f'=IF(AND($B$8<>"",E{r1}<$B$8),"Rechnung zu früh!","")')
    _formula(ws, f"F{r2}", f'=IF(F{r1}="NEIN!","O.g. Rechnung ist nicht förderfähig!","↑ ok")')
    _label(ws, f"G{r2}", "Skonto", bold=False)
    _inp(ws, f"H{r2}", row.skonto_rate, fmt=_PCT)
    _formula(ws, f"J{r2}", f'=IF(F{r1}="ja",H{r1}-(H{r1}*H{r2}),0)', fmt=_EUR)

    # Category rows: the split the Kostenaufstellung percentages prescribe; an
    # unusable row (duplicate / netto 0) gets NO category so it cannot reach a sum.
    cats = [] if row.unusable else list(row.splits)
    if not cats and not row.unusable:
        _inp(ws, f"K{r2}", "?")                                           # no split assignable → reviewer decides
        _inp(ws, f"L{r2}", None, fmt=_PCT)
    for k, split in enumerate(cats[:2]):
        rr = r2 + k
        _inp(ws, f"K{rr}", split.category)
        _inp(ws, f"L{rr}", split.anteil, fmt=_PCT)
        _formula(ws, f"M{rr}", f'=IF(K{rr}="IK",$J${r2}*L{rr},0)', fmt=_EUR)
        _formula(ws, f"N{rr}", f'=IF(K{rr}="NK",$J${r2}*L{rr},0)', fmt=_EUR)
        _formula(ws, f"O{rr}", f'=IF(K{rr}="EK",$J${r2}*L{rr},0)', fmt=_EUR)

    # Conditional formatting: the offending cell goes red, nothing else.
    _red_when(ws, f"C{r1}", f'AND(C{r1}<>"",$B$4<>"",C{r1}<$B$4)')
    _red_when(ws, f"E{r1}", f'AND(E{r1}<>"",$B$8<>"",E{r1}<$B$8)')
    _red_when(ws, f"F{r1}", f'F{r1}="NEIN!"')
    _red_when(ws, f"H{r1}", f'AND(X{r1}<>"",ABS(H{r1}-X{r1})>0.02)')
    _red_when(ws, f"K{r2}", f'K{r2}="?"')
    _red_when(ws, f"A{r1}:B{r1}", f'OR(ISNUMBER(SEARCH("Duplikat",$I{r1})),ISNUMBER(SEARCH("unbrauchbar",$I{r1})))')
    _red_when(ws, f"G{r1}", f'ISNUMBER(SEARCH("Kreuzsumme",$I{r1}))')
    return r3 + 2


def write_vne_tabelle(result: VneResult, config: ProjektConfig, output_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    _header(ws, config)

    r = FIRST_BLOCK_ROW
    first_block = r
    for row in result.invoices:
        r = _block(ws, r, row, result)
    last_block_row = max(r - 2, first_block)

    # Template terminator question, as in the consultant's sheets — also the marker
    # the ground-truth reader uses to end per-invoice accumulation before the Σ row.
    _label(ws, f"A{r}", "Wurde durch die durchgeführte Fördermaßnahme die Energieeinsparung erreicht?", bold=False, wrap=True)
    r += 1

    sum_row = r
    rng = lambda col: f"{col}{first_block}:{col}{last_block_row}"  # noqa: E731
    _label(ws, f"I{sum_row}", "Σ gesamt")
    _formula(ws, f"M{sum_row}", f"=SUM({rng('M')})", fmt=_EUR, bold=True)
    _formula(ws, f"N{sum_row}", f"=SUM({rng('N')})", fmt=_EUR, bold=True)
    _formula(ws, f"O{sum_row}", f"=SUM({rng('O')})", fmt=_EUR, bold=True)
    _formula(ws, f"J{sum_row}", f"=M{sum_row}+N{sum_row}+O{sum_row}", fmt=_EUR, bold=True)
    _inp(ws, f"Q{sum_row}", result.beantragt_ik, fmt=_EUR)
    _inp(ws, f"R{sum_row}", result.beantragt_nk, fmt=_EUR)
    _inp(ws, f"S{sum_row}", result.beantragt_ek, fmt=_EUR)
    _formula(ws, f"T{sum_row}", f'=IF(Q{sum_row}="",M{sum_row},MIN(Q{sum_row},M{sum_row}))', fmt=_EUR, bold=True)
    _formula(ws, f"U{sum_row}", f'=IF(R{sum_row}="",N{sum_row},MIN(R{sum_row},N{sum_row}))', fmt=_EUR, bold=True)
    _formula(ws, f"V{sum_row}", f'=IF(S{sum_row}="",O{sum_row},MIN(S{sum_row},O{sum_row}))', fmt=_EUR, bold=True)
    r = sum_row + 2

    b = config.bescheid
    if b is not None:
        agvo_row, mehr_row, anteil_row, max_row, besch_row, tats_row = r, r + 1, r + 2, r + 3, r + 4, r + 5
        _label(ws, f"I{agvo_row}", "AGVO Referenzkosten", bold=False)
        _inp(ws, f"J{agvo_row}", b.agvo_referenzkosten, fmt=_EUR)
        _label(ws, f"I{mehr_row}", "förderfähige Mehrkosten gemäß Rechnungen", bold=False)
        _formula(ws, f"J{mehr_row}", f'=J{sum_row}-IF(J{agvo_row}="",0,J{agvo_row})', fmt=_EUR)
        _label(ws, f"I{anteil_row}", "Kostendeckel-Förderanteil gemäß ESK", bold=False)
        _inp(ws, f"J{anteil_row}", b.kostendeckel_foerderanteil, fmt=_PCT)
        _label(ws, f"I{max_row}", "maximaler Förderbetrag gemäß Rechnungen", bold=False)
        _formula(ws, f"J{max_row}", f'=IF(J{anteil_row}="","",ROUND(J{mehr_row}*J{anteil_row},2))', fmt=_EUR)
        _label(ws, f"I{besch_row}", "Förderbetrag gemäß Zuwendungsbescheid", bold=False)
        _formula(ws, f"J{besch_row}", "=D6", fmt=_EUR)
        _label(ws, f"I{tats_row}", "Förderbetrag tatsächlich (niedrigerer Wert)")
        _formula(ws, f"J{tats_row}", f'=IF(OR(J{max_row}="",J{besch_row}=""),"",MIN(J{max_row},J{besch_row}))', fmt=_EUR, bold=True)
        r = tats_row + 2

    if result.ignored:
        _label(ws, f"A{r}", "Ignorierte Dokumente (keine Rechnung/kein Angebot):")
        r += 1
        for path in result.ignored:
            ws.cell(r, 1, path.name)
            r += 1

    widths = {"A": 40, "B": 44, "C": 13, "D": 13, "E": 13, "F": 14, "G": 14, "H": 14, "I": 44, "J": 14,
              "K": 8, "L": 10, "M": 14, "N": 14, "O": 14, "Q": 12, "R": 12, "S": 12, "T": 12, "U": 12, "V": 12,
              "W": 10, "X": 14}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = f"A{FIRST_BLOCK_ROW}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"

    if result.abgleich is not None:
        _write_abgleich(wb.create_sheet(ABGLEICH_SHEET_NAME), result.abgleich)

    wb.save(output_path)


def _write_abgleich(ws: Worksheet, abgleich: AbgleichResult) -> None:
    ws.cell(1, 1, "Positionsabgleich Angebot ↔ Rechnungen (Vorschlag — bitte prüfen)").font = _BOLD
    if abgleich.skipped_reason:
        ws.cell(2, 1, abgleich.skipped_reason).fill = _YELLOW_FILL
        ws.column_dimensions["A"].width = 120
        return
    ws.cell(2, 1, f"Angebotspositionen aus: {abgleich.offer_source}")

    labels = ["Pos", "Beschreibung (Angebot)", "angeboten €", "abgerechnet €", "Abweichung €",
              "Abw. %", "Konfidenz", "Rechnungszeilen", "Hinweis"]
    for c, label in enumerate(labels, start=1):
        ws.cell(4, c, label).font = _BOLD

    r = 6
    for g in abgleich.groups:
        ws.cell(r, 1, f"Lieferant: {g.label}").font = _BOLD
        r += 1
        for row in g.rows:
            pos = row.offer_position
            ws.cell(r, 1, pos.pos)
            ws.cell(r, 2, pos.description).alignment = _WRAP
            _money(ws, r, 3, row.offered)
            if not row.matched:
                if pos.optional:
                    ws.cell(r, 9, "optional, nicht abgerufen")
                else:
                    ws.cell(r, 9, "FEHLT — keine Rechnungszeile zugeordnet").fill = _RED_FILL
                r += 1
                continue
            _money(ws, r, 4, row.invoiced)
            variance = row.variance
            _money(ws, r, 5, variance)
            if variance is not None and row.offered not in (None, Decimal(0)):
                ws.cell(r, 6, float(variance / row.offered)).number_format = _PCT_DISPLAY
            # Q1 (2026-09-03): ANY variance ≠ 0 → red, consultant decides.
            if variance is not None and variance != 0:
                ws.cell(r, 5).fill = _RED_FILL
                ws.cell(r, 6).fill = _RED_FILL
            # "high" < "medium" alphabetically, so max() surfaces the weakest link.
            conf = max(m.confidence.value for m in row.matched)
            ws.cell(r, 7, conf)
            if conf != "high":
                ws.cell(r, 7).fill = _YELLOW_FILL
            ws.cell(r, 8, "; ".join(
                f"{m.invoice.invoice_number}#{m.invoice.positions[m.position_index].pos or m.position_index + 1}"
                for m in row.matched
            )).alignment = _WRAP
            notes = "; ".join(dict.fromkeys(m.note for m in row.matched if m.note))
            if notes:
                ws.cell(r, 9, notes).alignment = _WRAP
            r += 1
        for extra in g.extras:
            inv_pos = extra.invoice.positions[extra.position_index]
            ws.cell(r, 1, "EXTRA").fill = _RED_FILL
            ws.cell(r, 2, inv_pos.description).alignment = _WRAP
            _money(ws, r, 4, extra.amount)
            ws.cell(r, 8, f"{extra.invoice.invoice_number}#{inv_pos.pos or extra.position_index + 1}")
            ws.cell(r, 9, extra.note or "keine Angebotsposition zugeordnet").fill = _RED_FILL
            r += 1

        ws.cell(r, 1, "Σ").font = _BOLD
        ws.cell(r, 2, f"Summe {g.label} (ohne optionale Positionen)").font = _BOLD
        _money(ws, r, 3, g.offered_total, bold=True)
        _money(ws, r, 4, g.invoiced_total, bold=True)
        diff = g.invoiced_total - g.offered_total
        _money(ws, r, 5, diff, bold=True)
        if diff != 0:
            ws.cell(r, 5).fill = _RED_FILL
        if not g.invoices:
            ws.cell(r, 9, "keine Rechnung dieses Lieferanten im Lauf").fill = _RED_FILL
        elif g.sonderpreis is not None:
            ws.cell(r, 9, f"Sonderpreis lt. Angebot: {format_de_decimal(g.sonderpreis)} €")
        r += 2

    if abgleich.offerless_invoices:
        ws.cell(r, 1, "Rechnungen ohne Angebots-Lieferant").font = _BOLD
        r += 1
        for inv in abgleich.offerless_invoices:
            ws.cell(r, 2, f"{inv.vendor_name} — {inv.invoice_number}")
            _money(ws, r, 4, inv.netto)
            ws.cell(r, 9, "kein Angebot dieses Lieferanten (ggf. lt. Schätzung)").fill = _RED_FILL
            r += 1

    for letter, width in {"A": 10, "B": 46, "C": 14, "D": 14, "E": 14, "F": 9,
                          "G": 10, "H": 26, "I": 44}.items():
        ws.column_dimensions[letter].width = width
