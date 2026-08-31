"""Render a VneResult into the VNE-Tabelle .xlsx (newest 3-category template layout).

Column layout mirrors the consultant's current template generation (Craemer/Thess):
A Empfänger/Referenz · B Belege · C Auftrag erteilt am · D Zahlungsdatum (blank in
v1) · E Rechnungsdatum · F Bewilligungszeitraum eingehalten · G Brutto · H Netto ·
I Anmerkung · J Netto m. Skto./Rab. · K IK/NK/EK · L Anteil · M/N/O Betrag Netto
IK/NK/EK · Q–S beantragt · T–V ansetzbar.

v1 writes cached VALUES, not live formulas: openpyxl cannot store a formula's
cached result, so a formula-bearing file would read as empty through any
`data_only` consumer (including our own ground-truth reader) until the consultant
recalculates in LibreOffice. Numbers-first beats formulas-first here; live
formulas are a polish candidate (raw-XML post-pass like the cost-estimation .ods writer).

Every failed check renders as a red-filled row with the ⚠ reason in the
Anmerkung column — same loud-not-silent rule as the cost-estimation workbook.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from invoice_controller.config import ProjektConfig
from invoice_controller.models import InvoiceType
from invoice_controller.vne.compute import VneResult

SHEET_NAME = "(Vorlage VNE-Maske)"

_HEADERS = [
    "Empfänger\nVerwendungszweck/Referenz\nBetreff",   # A
    "Ausgabenbelege (Rechnungsnummer, Typ)",           # B
    "Auftrag erteilt am",                              # C
    "Zahlungsdatum",                                   # D
    "Rechnungsdatum",                                  # E
    "Bewilligungs-zeitraum eingehalten",               # F
    "Betrag\nBrutto",                                  # G
    "Betrag\nNetto",                                   # H
    "Anmerkung Bezug",                                 # I
    "Betrag Netto m. Skto./Rab.",                      # J
    "IK oder \nNK oder\nEK",                           # K
    "Anteil IK bzw. NK bzw. EK",                       # L
    "Betrag Netto\nInvestitions-kosten",               # M
    "Betrag Netto\nNebenkosten",                       # N
    "Betrag Netto\nEinsparkonzept",                    # O
    "",                                                # P
    "beantragt\nIK",                                   # Q
    "beantragt\nNK",                                   # R
    "beantragt\nEK",                                   # S
    "ansetzbar\nIK",                                   # T
    "ansetzbar\nNK",                                   # U
    "ansetzbar\nEK",                                   # V
]

_EUR = "#,##0.00\\ €"
_DATE = "DD.MM.YYYY"
_PCT = "0.0000"
_RED_FILL = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
_BOLD = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")

_TYPE_LABEL = {
    InvoiceType.RECHNUNG: "Rechnung",
    InvoiceType.ANZAHLUNGSRECHNUNG: "Anzahlungsrechnung",
    InvoiceType.TEILRECHNUNG: "Teil-/Abschlagsrechnung",
    InvoiceType.SCHLUSSRECHNUNG: "Schlussrechnung",
    InvoiceType.GUTSCHRIFT: "Gutschrift",
}


def _money(ws: Worksheet, row: int, col: int, value: Decimal | None, *, bold: bool = False) -> None:
    if value is None:
        return
    c = ws.cell(row, col, float(value))
    c.number_format = _EUR
    if bold:
        c.font = _BOLD


def write_vne_tabelle(result: VneResult, config: ProjektConfig, output_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SHEET_NAME

    r = 1
    if config.client is not None:
        ws.cell(r, 1, "Antragsteller").font = _BOLD
        ws.cell(r, 2, f"{config.client.name}" + (f", {config.client.address}" if config.client.address else ""))
        r += 1
    if config.has_window:
        ws.cell(r, 1, "Bewilligungszeitraum").font = _BOLD
        ws.cell(r, 2, config.bewilligungszeitraum_start).number_format = _DATE
        ws.cell(r, 3, "bis")
        ws.cell(r, 4, config.bewilligungszeitraum_end).number_format = _DATE
        r += 1
    if config.bescheid is not None and config.bescheid.foerderbetrag is not None:
        ws.cell(r, 1, "Förderbetrag lt. Zuwendungsbescheid").font = _BOLD
        _money(ws, r, 2, config.bescheid.foerderbetrag)
        r += 1
    r += 1

    header_row = r
    for col, text in enumerate(_HEADERS, start=1):
        if text:
            c = ws.cell(header_row, col, text)
            c.font = _BOLD
            c.alignment = _WRAP
    r = header_row + 1

    for row in result.invoices:
        inv = row.invoice
        flagged = bool(row.flags)
        hdr = r

        ref = f"{inv.vendor_name} Rg {inv.invoice_number}; {inv.invoice_date.strftime('%d.%m.%Y')}"
        if inv.subject:
            ref += f"; {inv.subject}"
        ws.cell(hdr, 1, ref).alignment = _WRAP
        ws.cell(hdr, 2, f"{inv.invoice_number} ({_TYPE_LABEL[inv.invoice_type]})")
        if inv.order_date is not None:
            ws.cell(hdr, 3, inv.order_date).number_format = _DATE
        # D Zahlungsdatum deliberately blank in v1
        ws.cell(hdr, 5, inv.invoice_date).number_format = _DATE
        if row.window_ok is not None:
            ws.cell(hdr, 6, "Ja" if row.window_ok else "Nein")
        # Brutto: stated; for steuerfrei invoices without one, netto (brutto == netto).
        _money(ws, hdr, 7, inv.brutto if inv.brutto is not None else inv.netto)
        _money(ws, hdr, 8, inv.netto)
        anmerkung = " | ".join(f"⚠ {f}" for f in row.flags) if flagged else (inv.subject or "")
        ws.cell(hdr, 9, anmerkung).alignment = _WRAP
        _money(ws, hdr, 10, row.netto_after_skonto)
        r += 1

        for split in row.splits:
            ws.cell(r, 11, split.category)
            c = ws.cell(r, 12, float(split.anteil))
            c.number_format = _PCT
            col = {"IK": 13, "NK": 14, "EK": 15}[split.category]
            _money(ws, r, col, split.amount)
            r += 1
        if not row.splits:
            # Red-flag row: no split — category/Anteil/amount cells stay EMPTY for
            # the consultant (decided 2026-08-24); the reason is in the Anmerkung.
            r += 1

        ws.cell(r, 7, "Skonto")
        ws.cell(r, 8, float(row.skonto_rate)).number_format = "0.00%"
        r += 1

        if flagged:
            for rr in range(hdr, r):
                for cc in range(1, 16):
                    ws.cell(rr, cc).fill = _RED_FILL
        r += 1

    # Template terminator question, as in the consultant's sheets — also the marker
    # the ground-truth reader uses to end per-invoice accumulation before the Σ row.
    ws.cell(r, 1, "Wurde durch die durchgeführte Fördermaßnahme die Energieeinsparung erreicht?").alignment = _WRAP
    r += 1

    sum_row = r
    ws.cell(sum_row, 9, "Σ gesamt").font = _BOLD
    _money(ws, sum_row, 10, result.sum_total, bold=True)
    _money(ws, sum_row, 13, result.sum_ik, bold=True)
    _money(ws, sum_row, 14, result.sum_nk, bold=True)
    _money(ws, sum_row, 15, result.sum_ek, bold=True)
    _money(ws, sum_row, 17, result.beantragt_ik, bold=True)
    _money(ws, sum_row, 18, result.beantragt_nk, bold=True)
    _money(ws, sum_row, 19, result.beantragt_ek, bold=True)
    _money(ws, sum_row, 20, result.ansetzbar("IK"), bold=True)
    _money(ws, sum_row, 21, result.ansetzbar("NK"), bold=True)
    _money(ws, sum_row, 22, result.ansetzbar("EK"), bold=True)
    r = sum_row + 2

    b = config.bescheid
    if b is not None:
        for label, value in [
            ("AGVO Referenzkosten", b.agvo_referenzkosten),
            ("förderfähige Mehrkosten gemäß Rechnungen", result.mehrkosten),
            ("Kostendeckel-Förderanteil gemäß ESK", b.kostendeckel_foerderanteil),
            ("maximaler Förderbetrag gemäß Rechnungen", result.max_foerderbetrag),
            ("Förderbetrag gemäß Zuwendungsbescheid", b.foerderbetrag),
            ("Förderbetrag tatsächlich (niedrigerer Wert)", result.foerderbetrag_tatsaechlich),
        ]:
            if value is None:
                continue
            ws.cell(r, 9, label)
            if label.startswith("Kostendeckel"):
                ws.cell(r, 10, float(value)).number_format = "0.00%"
            else:
                _money(ws, r, 10, value)
            r += 1
        r += 1

    if result.ignored:
        ws.cell(r, 1, "Ignorierte Dokumente (keine Rechnung/kein Angebot):").font = _BOLD
        r += 1
        for path in result.ignored:
            ws.cell(r, 1, path.name)
            r += 1

    widths = {1: 52, 2: 26, 3: 13, 4: 13, 5: 13, 6: 13, 7: 13, 8: 13, 9: 40, 10: 14,
              11: 8, 12: 10, 13: 14, 14: 14, 15: 14, 17: 12, 18: 12, 19: 12, 20: 12, 21: 12, 22: 12}
    for col, width in widths.items():
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    wb.save(output_path)
