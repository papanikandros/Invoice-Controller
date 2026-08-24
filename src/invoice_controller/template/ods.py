from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from odfdo import Cell, Document, Element, Table

from invoice_controller.models import (
    CrossSumCheck,
    DocumentKind,
    Kostenkategorie,
    OfferDocument,
    Position,
)
from invoice_controller.narrative import render_narrative_blocks
from invoice_controller.normalize import format_de_decimal

# Shipped code asset (sanitized placeholder headers). The real project template
# stays in the gitignored examples/ corpus; the writer only reuses styles + block shape.
TEMPLATE_PATH = Path(__file__).parent / "template.ods"

HEADER_COLS = [
    "Position",
    "Beschreibung",
    "Gesamtkosten",
    "Investitionskosten",
    "Nebenkosten",
    "Nachlass",
]

# Light Yellow 3 — applied ONLY to the four cost columns in position rows.
LIGHT_YELLOW_3 = "#FFFF99"
# Cross-sum warning row background/text — loud on purpose: a block whose Σ does not
# reconcile with the document must be unmistakable inside the workbook itself.
LIGHT_RED = "#FFCCCC"
WARN_RED = "#CC0000"

S_HEADER_BOLD = "ce2"           # SOLL header + column header row (bold centered, from template)
S_POS_NUM = "IC_POS_NUM"        # Position number column — white, centered
S_DESC = "IC_DESC"              # Beschreibung column — white, left-aligned
S_MONEY = "IC_MONEY"            # Money columns in position rows — yellow, centered, German EUR
S_SUM_LABEL = "IC_SUM_LABEL"    # Σ row's Pos and Beschreibung cells — white, bold, underlined
S_SUM_NUM = "IC_SUM_NUM"        # Σ row's money cells — white, bold, underlined, centered, EUR
S_SPP_LABEL = "IC_SPP_LABEL"    # Sonderpreis row's label cell — white, bold, centered (no underline)
S_SPP_NUM = "IC_SPP_NUM"        # Sonderpreis row's money cells — white, bold, centered, EUR
S_PCT = "IC_PCT"                # Percentage row — white, centered, German % with grouping
S_NARRATIVE = "IC_NARRATIVE"    # Cost-description text blocks on the Kostenbeschreibung sheet
S_WARN = "IC_WARN"              # Cross-sum warning row — bold red on light red, merged A–F

# The cost descriptions live on their own sheet so the Kostenaufstellung layout stays clean.
DESC_SHEET_NAME = "Kostenbeschreibung"
DESC_COL_END = 5            # text blocks span columns A–F, same width as the SOLL header
NAR_MIN_ROWS = 3
NAR_CHARS_PER_ROW = 50

# Custom number formats with German locale set on the data-style root, not just on the
# currency-symbol child. The template's N172 has language on the currency-symbol only,
# which causes LibreOffice to format the number portion (1.234,56 vs 1,234.56) using the
# user's system locale instead of de_DE.
S_NUMSTYLE_EUR = "IC_N_EUR_GRP"
S_NUMSTYLE_PCT = "IC_N_PCT_GRP"


def write_kostenaufstellung(
    offers: list[OfferDocument],
    output_path: Path,
    *,
    template_path: Path = TEMPLATE_PATH,
) -> None:
    """Render offer blocks into Kostenaufstellung.ods. Numeric cells get German EUR /
    grouped percentage formats AND pre-formatted display text so the file is correct
    regardless of LibreOffice's locale at open time. Only the position-row money
    columns get a Light Yellow 3 background. SOLL headers span 6 columns. If an offer
    has a Sonderpreis (or preisnachlass implying one), an extra row is inserted after
    the Σ row to surface the negotiated total and the Nachlass (= Gesamt − Sonderpreis).

    When any offer carries a cost narrative, a second sheet 'Kostenbeschreibung' is added
    holding the Investitionskosten / Nebenkosten prose blocks per offer, keeping the
    Kostenaufstellung cost table itself untouched.
    """
    doc = Document(str(template_path))
    _ensure_custom_styles(doc)

    body = doc.body
    old_table = body.tables[0]
    sheet_name = old_table.name
    body.delete(old_table)

    table = Table(sheet_name)
    row = 0
    for idx, offer in enumerate(offers):
        if idx > 0:
            row += 1
        row = _append_offer_block(table, offer, row)

    body.append(table)

    description = _build_description_table(offers)
    if description is not None:
        body.append(description)

    doc.save(str(output_path))


def _append_offer_block(table: Table, offer: OfferDocument, start_row: int) -> int:
    soll_row = start_row
    _put_text(table, 0, soll_row, _format_soll_header(offer), S_HEADER_BOLD)
    table.set_span((0, soll_row, 5, soll_row))

    col_row = soll_row + 1
    for c, name in enumerate(HEADER_COLS):
        _put_text(table, c, col_row, name, S_HEADER_BOLD)

    pos_first = col_row + 1
    for i, pos in enumerate(offer.positions):
        r = pos_first + i
        _put_text(table, 0, r, pos.pos, S_POS_NUM)
        _put_text(table, 1, r, _position_label(pos), S_DESC)
        if pos.line_total_net is None:
            # Rate-card optional (e.g. an Eventualposition priced "95,00 €/m" with no
            # quantity): no computable line total. The rate lives in the description;
            # leave the money cells blank so the Σ SUM range simply skips it.
            for c in (2, 3, 4, 5):
                _put_text(table, c, r, "", S_MONEY)
            continue
        inv, neb, nac = _category_split(pos, pos.line_total_net)
        _put_money(table, 2, r, Decimal(pos.line_total_net), S_MONEY)
        _put_money(table, 3, r, Decimal(inv), S_MONEY)
        _put_money(table, 4, r, Decimal(neb), S_MONEY)
        _put_money(table, 5, r, Decimal(nac), S_MONEY)

    n = len(offer.positions)
    sum_row = pos_first + n
    pos_first_1based = pos_first + 1
    pos_last_1based = sum_row
    sum_row_1based = sum_row + 1

    last_pos_label = offer.positions[-1].pos if offer.positions else ""
    _put_text(table, 0, sum_row, f"Σ 1…{last_pos_label}", S_SUM_LABEL)
    _put_text(table, 1, sum_row, f"Gesamtpreis Pos.1 – Pos.{last_pos_label}", S_SUM_LABEL)

    sums = _column_sums(offer)
    _put_money_formula(
        table, 2, sum_row,
        f"of:=SUM([.C{pos_first_1based}:.C{pos_last_1based}])",
        sums["gesamt"], S_SUM_NUM,
    )
    _put_money_formula(
        table, 3, sum_row,
        f"of:=SUM([.D{pos_first_1based}:.D{pos_last_1based}])",
        sums["inv"], S_SUM_NUM,
    )
    _put_money_formula(
        table, 4, sum_row,
        f"of:=SUM([.E{pos_first_1based}:.E{pos_last_1based}])",
        sums["neb"], S_SUM_NUM,
    )
    _put_money_formula(
        table, 5, sum_row,
        f"of:=[.C{sum_row_1based}]-[.D{sum_row_1based}]-[.E{sum_row_1based}]",
        sums["nac"], S_SUM_NUM,
    )

    check = offer.cross_sum
    cross_failed = not check.passed and not check.not_applicable

    next_row = sum_row + 1
    if cross_failed and check.expected is not None:
        # The document's own stated Nettosumme next to the computed Σ, so a vendor-side
        # arithmetic error (or a dropped/mis-captured position) is visible in the workbook
        # itself, not only in the CLI output at extraction time.
        _put_text(table, 0, next_row, "", S_SPP_LABEL)
        _put_text(table, 1, next_row, "Nettosumme lt. Dokument", S_SPP_LABEL)
        _put_money(table, 2, next_row, check.expected, S_SPP_NUM)
        for c in (3, 4, 5):
            _put_text(table, c, next_row, "", S_SPP_NUM)
        next_row += 1

    sonderpreis = _effective_sonderpreis(offer)
    spp_nachlass: Decimal | None = None
    if sonderpreis is not None:
        spp_row = next_row
        spp_row_1based = spp_row + 1
        spp_nachlass = sums["gesamt"] - sonderpreis

        _put_text(table, 0, spp_row, "", S_SPP_LABEL)
        _put_text(table, 1, spp_row, f"Sonderpreis Pos.1 – Pos.{last_pos_label}", S_SPP_LABEL)
        _put_money(table, 2, spp_row, sonderpreis, S_SPP_NUM)
        _put_money_formula(
            table, 3, spp_row,
            f"of:=[.D{sum_row_1based}]",
            sums["inv"], S_SPP_NUM,
        )
        _put_money_formula(
            table, 4, spp_row,
            f"of:=[.E{sum_row_1based}]",
            sums["neb"], S_SPP_NUM,
        )
        _put_money_formula(
            table, 5, spp_row,
            f"of:=[.C{sum_row_1based}]-[.C{spp_row_1based}]",
            spp_nachlass, S_SPP_NUM,
        )

        pct_row = spp_row + 1
        nachlass_ref_row = spp_row_1based
        nachlass_for_pct = spp_nachlass
    else:
        pct_row = next_row
        nachlass_ref_row = sum_row_1based
        nachlass_for_pct = sums["nac"]

    gesamt_f = float(sums["gesamt"])
    inv_ratio = float(sums["inv"]) / gesamt_f if gesamt_f else 0.0
    neb_ratio = float(sums["neb"]) / gesamt_f if gesamt_f else 0.0
    nachlass_ratio = float(nachlass_for_pct) / gesamt_f if gesamt_f else 0.0
    total_ratio = 1.0 if gesamt_f else 0.0

    _put_text(table, 0, pct_row, "", S_SUM_LABEL)
    _put_text(table, 1, pct_row, "", S_SUM_LABEL)
    _put_pct_formula(
        table, 2, pct_row,
        f"of:=[.C{sum_row_1based}]/[.C{sum_row_1based}]",
        total_ratio, S_PCT,
    )
    _put_pct_formula(
        table, 3, pct_row,
        f"of:=[.D{sum_row_1based}]/[.C{sum_row_1based}]",
        inv_ratio, S_PCT,
    )
    _put_pct_formula(
        table, 4, pct_row,
        f"of:=[.E{sum_row_1based}]/[.C{sum_row_1based}]",
        neb_ratio, S_PCT,
    )
    _put_pct_formula(
        table, 5, pct_row,
        f"of:=[.F{nachlass_ref_row}]/[.C{sum_row_1based}]",
        nachlass_ratio, S_PCT,
    )

    end_row = pct_row + 1
    if cross_failed:
        _put_text(table, 0, end_row, _cross_sum_warning_text(check), S_WARN)
        table.set_span((0, end_row, 5, end_row))
        end_row += 1

    return end_row


def _cross_sum_warning_text(check: CrossSumCheck) -> str:
    """German one-liner for the in-workbook warning row of an unreconciled block."""
    if check.expected is None:
        return (
            "⚠ KREUZSUMME NICHT PRÜFBAR: keine Nettosumme im Dokument gefunden — "
            f"Σ Positionen = {format_de_decimal(check.actual)} € manuell gegen das PDF prüfen"
        )
    # Report the position sum that came closest to the stated total (the check accepts
    # either the mandatory-only Σ or the Σ incl. optionals — see cross_sum.check_offer).
    actual = check.actual
    if check.actual_incl_optional is not None and abs(check.actual_incl_optional - check.expected) < abs(
        actual - check.expected
    ):
        actual = check.actual_incl_optional
    diff = actual - check.expected
    return (
        f"⚠ KREUZSUMME WEICHT AB: Σ Positionen = {format_de_decimal(actual)} € vs. "
        f"Nettosumme lt. Dokument = {format_de_decimal(check.expected)} € "
        f"(Differenz {format_de_decimal(diff)} €) — Positionen manuell gegen das PDF prüfen"
    )


def _estimate_rows(text: str) -> int:
    lines = -(-len(text) // NAR_CHARS_PER_ROW)  # ceil division
    return max(NAR_MIN_ROWS, lines)


def _build_description_table(offers: list[OfferDocument]) -> Table | None:
    """Build the 'Kostenbeschreibung' sheet: per offer, the same SOLL header as on the
    Kostenaufstellung sheet followed by the Investitionskosten / Nebenkosten prose blocks
    (each a merged, word-wrapped cell spanning columns A–F). Returns None when no offer has
    any narrative, so a description-free run doesn't add an empty sheet."""
    rendered = [(offer, render_narrative_blocks(offer)) for offer in offers]
    rendered = [(offer, blocks) for offer, blocks in rendered if blocks[0] or blocks[1]]
    if not rendered:
        return None

    table = Table(DESC_SHEET_NAME)
    row = 0
    for idx, (offer, (inv_text, neb_text)) in enumerate(rendered):
        if idx > 0:
            row += 1  # blank spacer row between offers
        _put_text(table, 0, row, _format_soll_header(offer), S_HEADER_BOLD)
        table.set_span((0, row, DESC_COL_END, row))
        row += 1
        for text in (inv_text, neb_text):
            if not text:
                continue
            rows = _estimate_rows(text)
            _put_text(table, 0, row, text, S_NARRATIVE)
            table.set_span((0, row, DESC_COL_END, row + rows - 1))
            row += rows
    return table


def _format_soll_header(offer: OfferDocument) -> str:
    vendor = offer.header.vendor_short or offer.header.vendor_name
    datum = offer.header.offer_date.strftime("%d.%m.%Y")
    if offer.kind is DocumentKind.STATEMENT:
        # A statement is still SOLL (the planned side), but it is an estimate, not a vendor
        # offer — label it so the consultant (and a BAFA reviewer) can see at a glance that
        # the figures rest on the client's assumption, not a priced offer, and are netto.
        text = f"SOLL: SCHÄTZUNG lt. Stellungnahme {vendor} vom {datum} (kein Angebot, netto angenommen)"
    else:
        text = f"SOLL: {vendor} Angebot {offer.header.offer_number} vom {datum}"
    if offer.header.title:
        text += f", {offer.header.title}"
    return text


def _position_label(pos: Position) -> str:
    """Append an '(optional)' tag to optional positions so the consultant can spot the
    keep/drop candidates inline, mirroring the consultant's own hand-built sheets. Skip if
    the description already carries an 'option' marker to avoid a doubled tag."""
    if pos.optional and "option" not in pos.description.lower():
        return f"{pos.description} (optional)"
    return pos.description


def _column_sums(offer: OfferDocument) -> dict[str, Decimal]:
    gesamt = inv = neb = Decimal(0)
    for p in offer.positions:
        total = p.line_total_net
        if total is None:  # rate-card optional with no computable total
            continue
        gesamt += total
        if p.kategorie == Kostenkategorie.INVESTITIONSKOSTEN:
            inv += total
        elif p.kategorie == Kostenkategorie.NEBENKOSTEN:
            neb += total
    nac = gesamt - inv - neb
    return {"gesamt": gesamt, "inv": inv, "neb": neb, "nac": nac}


def _effective_sonderpreis(offer: OfferDocument) -> Decimal | None:
    """Return the Sonderpreis (negotiated final price) to display, deriving from
    preisnachlass if no explicit sonderpreis is set. Returns None when neither is present.
    A final price is by definition positive — OfferTotals normalizes LLM output, but the
    derived difference can still be nonsense when the extraction is broken, and rendering
    a negative 'Sonderpreis' would be worse than rendering none."""
    totals = offer.totals
    candidate: Decimal | None = None
    if totals.sonderpreis is not None:
        candidate = totals.sonderpreis
    elif totals.preisnachlass and totals.nettosumme is not None:
        candidate = totals.nettosumme - totals.preisnachlass
    if candidate is not None and candidate <= 0:
        return None
    return candidate


def _category_split(pos: Position, line_total: Decimal) -> tuple[float, float, float]:
    total = float(line_total)
    if pos.kategorie == Kostenkategorie.NEBENKOSTEN:
        return 0.0, total, 0.0
    if pos.kategorie == Kostenkategorie.NACHLASS:
        return 0.0, 0.0, total
    return total, 0.0, 0.0


# ---------------------------------------------------------------------------
# Cell-writing helpers (pre-format display text in German style for reliability)
# ---------------------------------------------------------------------------

def _format_eur_display(value: Decimal) -> str:
    return f"{format_de_decimal(value)} €"


def _put_text(table: Table, col: int, row: int, value: str, style: str) -> None:
    cell = Cell(style=style) if value in (None, "") else Cell(value=value, style=style)
    table.set_cell((col, row), cell)


def _put_money(table: Table, col: int, row: int, value: Decimal, style: str) -> None:
    cell = Cell(value=float(value), text=_format_eur_display(value), style=style)
    table.set_cell((col, row), cell)


def _put_money_formula(
    table: Table, col: int, row: int, formula: str, cached: Decimal, style: str,
) -> None:
    cell = Cell(value=float(cached), text=_format_eur_display(cached), style=style)
    cell.formula = formula
    table.set_cell((col, row), cell)


def _put_pct_formula(
    table: Table, col: int, row: int, formula: str, cached_ratio: float, style: str,
) -> None:
    """Set a percentage cell with a precomputed cached value so LibreOffice displays
    the correct percentage on open, before any manual recalc. ODF stores percentages as
    ratios (1.0 = 100 %); the display text is the percentage * 100 in German format."""
    pct_value = Decimal(str(cached_ratio * 100))
    text = f"{format_de_decimal(pct_value)} %"
    cell = Cell(value=cached_ratio, text=text, style=style)
    cell.formula = formula
    table.set_cell((col, row), cell)


# ---------------------------------------------------------------------------
# Custom styles injection
# ---------------------------------------------------------------------------

_PCT_GROUPED_XML = (
    '<number:percentage-style '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    f'style:name="{S_NUMSTYLE_PCT}" number:language="de" number:country="DE">'
    '<number:number number:decimal-places="2" number:min-decimal-places="2" '
    'number:min-integer-digits="1" number:grouping="true"/>'
    '<number:text> %</number:text>'
    '</number:percentage-style>'
)

# Two currency styles: one for negative values (no parens, leading sign), one for positive.
# We map negative -> positive via style:map so a single style ref handles both signs.
_EUR_GROUPED_POS_XML = (
    '<number:currency-style '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    f'style:name="{S_NUMSTYLE_EUR}P" style:volatile="true" '
    'number:language="de" number:country="DE">'
    '<number:number number:decimal-places="2" number:min-decimal-places="2" '
    'number:min-integer-digits="1" number:grouping="true"/>'
    '<number:text> </number:text>'
    '<number:currency-symbol number:language="de" number:country="DE">€</number:currency-symbol>'
    '</number:currency-style>'
)

_EUR_GROUPED_XML = (
    '<number:currency-style '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    f'style:name="{S_NUMSTYLE_EUR}" number:language="de" number:country="DE">'
    '<style:text-properties fo:color="#FF0000"/>'
    '<number:text>-</number:text>'
    '<number:number number:decimal-places="2" number:min-decimal-places="2" '
    'number:min-integer-digits="1" number:grouping="true"/>'
    '<number:text> </number:text>'
    '<number:currency-symbol number:language="de" number:country="DE">€</number:currency-symbol>'
    f'<style:map style:condition="value()&gt;=0" style:apply-style-name="{S_NUMSTYLE_EUR}P"/>'
    '</number:currency-style>'
)


def _build_cell_style_xml(
    name: str,
    *,
    halign: str = "center",
    weight: str = "normal",
    data_style: str | None = None,
    background: str | None = None,
    underline: bool = False,
    wrap: bool = False,
    valign: str = "middle",
    color: str | None = None,
) -> str:
    data_attr = f' style:data-style-name="{data_style}"' if data_style else ""
    bg_attr = f' fo:background-color="{background}"' if background else ""
    wrap_attr = ' fo:wrap-option="wrap"' if wrap else ""
    color_attr = f' fo:color="{color}"' if color else ""
    underline_attrs = (
        ' style:text-underline-style="solid" style:text-underline-width="auto"'
        ' style:text-underline-color="font-color"'
        if underline
        else ""
    )
    return (
        '<style:style xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
        f'style:name="{name}" style:family="table-cell" style:parent-style-name="Default"{data_attr}>'
        f'<style:table-cell-properties{bg_attr} style:vertical-align="{valign}"{wrap_attr} '
        'fo:border="0.74pt solid #000000" style:text-align-source="fix"/>'
        f'<style:paragraph-properties fo:text-align="{halign}"/>'
        # Explicit de_DE locale prevents LibreOffice from falling back to the user's UI
        # locale when formatting numbers / currency / percentage.
        '<style:text-properties style:font-name="Arial" fo:font-size="10pt" '
        f'fo:font-weight="{weight}" fo:language="de" fo:country="DE"{color_attr}{underline_attrs}/>'
        '</style:style>'
    )


def _ensure_custom_styles(doc: Document) -> None:
    existing = {s.get_attribute("style:name") for s in doc.body.get_elements("//style:style")}
    existing |= {
        s.get_attribute("style:name")
        for s in doc.body.get_elements("//number:percentage-style")
    }

    automatic_styles = doc.body.get_element("//office:automatic-styles")

    if S_NUMSTYLE_PCT not in existing:
        automatic_styles.append(Element.from_tag(_PCT_GROUPED_XML))
    if f"{S_NUMSTYLE_EUR}P" not in existing:
        automatic_styles.append(Element.from_tag(_EUR_GROUPED_POS_XML))
    if S_NUMSTYLE_EUR not in existing:
        automatic_styles.append(Element.from_tag(_EUR_GROUPED_XML))

    custom = {
        S_POS_NUM: dict(halign="center"),
        S_DESC: dict(halign="start"),
        S_MONEY: dict(halign="center", data_style=S_NUMSTYLE_EUR, background=LIGHT_YELLOW_3),
        S_SUM_LABEL: dict(halign="center", weight="bold", underline=True),
        S_SUM_NUM: dict(halign="center", weight="bold", underline=True, data_style=S_NUMSTYLE_EUR),
        S_SPP_LABEL: dict(halign="center", weight="bold"),
        S_SPP_NUM: dict(halign="center", weight="bold", data_style=S_NUMSTYLE_EUR),
        S_PCT: dict(halign="center", data_style=S_NUMSTYLE_PCT),
        S_NARRATIVE: dict(halign="start", valign="top", wrap=True),
        S_WARN: dict(halign="start", weight="bold", background=LIGHT_RED, color=WARN_RED, wrap=True),
    }
    for name, opts in custom.items():
        if name in existing:
            continue
        automatic_styles.append(Element.from_tag(_build_cell_style_xml(name, **opts)))
