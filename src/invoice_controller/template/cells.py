"""Cell vocabulary shared by the EEW VNE-Maske and the BEG Kostenzusammenstellung writers
(Phase 2c V3/V4, shared since V7 2026-10-05).

Three kinds of cells, and nothing else: an INPUT (an extracted or typed value, yellow
`FFFFFF99` exactly like every consultant sheet), a FORMULA (derived, no fill — the
reviewer follows the logic in the cell), a LABEL. Colour that carries a verdict is
NEVER written as a fill by us; it is conditional formatting, so a corrected input
clears it. Number formats are copied from the consultant workbooks.
"""

from __future__ import annotations

from decimal import Decimal

from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

EUR = "#,##0.00\\ [$€-407];[RED]\\-#,##0.00\\ [$€-407]"
DATE = "DD.MM.YYYY"
PCT = "0.00\\\xa0%"

INPUT_FILL = PatternFill(start_color="FFFF99", end_color="FFFF99", fill_type="solid")
CF_RED = PatternFill(start_color="FF9999", end_color="FF9999", fill_type="solid", bgColor="FF9999")
CF_GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid", bgColor="C6EFCE")
CF_YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid", bgColor="FFEB9C")
BOLD = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")


def inp(ws: Worksheet, ref: str, value, *, fmt: str | None = None):
    """An INPUT cell; None leaves it empty but still yellow (the reviewer sees the gap)."""
    c = ws[ref]
    if value is not None:
        c.value = float(value) if isinstance(value, Decimal) else value
    c.fill = INPUT_FILL
    if fmt:
        c.number_format = fmt
    return c


def formula(ws: Worksheet, ref: str, expr: str, *, fmt: str | None = None, bold: bool = False):
    c = ws[ref]
    c.value = expr
    if fmt:
        c.number_format = fmt
    if bold:
        c.font = BOLD
    return c


def label(ws: Worksheet, ref: str, text: str, *, bold: bool = True, wrap: bool = False):
    c = ws[ref]
    c.value = text
    if bold:
        c.font = BOLD
    if wrap:
        c.alignment = WRAP
    return c


def fill_when(ws: Worksheet, cell_range: str, expr: str, fill: PatternFill) -> None:
    ws.conditional_formatting.add(cell_range, FormulaRule(formula=[expr], fill=fill, stopIfTrue=False))


def red_when(ws: Worksheet, cell_range: str, expr: str) -> None:
    fill_when(ws, cell_range, expr, CF_RED)


def landscape_fit_to_width(ws: Worksheet, *, header_row: int | None = None, a3: bool = False) -> None:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3 if a3 else ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    if header_row:
        ws.print_title_rows = f"{header_row}:{header_row}"
