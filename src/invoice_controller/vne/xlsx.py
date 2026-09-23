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

The scope check (vne/abgleich.py) lives on a SECOND sheet, `Positionsabgleich`:
the VNE-Maske sheet must stay identical to the consultant's examples (user
requirement 2026-09-21), so nothing of the Abgleich is written into it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from invoice_controller.config import ProjektConfig
from invoice_controller.models import InvoiceType
from invoice_controller.normalize import format_de_decimal
from invoice_controller.vne.abgleich import AbgleichResult
from invoice_controller.vne.compute import VneResult

SHEET_NAME = "(Vorlage VNE-Maske)"
ABGLEICH_SHEET_NAME = "Positionsabgleich"

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
_YELLOW_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
_PCT_DISPLAY = "0.0%"
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
