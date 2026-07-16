"""Read a Kostenaufstellung-style .ods into a normalised Python structure for comparison.

Used both to load ground-truth `.ods` (from examples/<project>/) and to load tool output for
cell-level regression assertions. Captures only the semantic data — vendor blocks, position
rows, group sums — not the styling.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

NS_T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
NS_P = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
NS_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"

SOLL_HEADER_RE = re.compile(
    r"SOLL[:\s]+(?P<rest>.+)$",
    re.IGNORECASE,
)


@dataclass
class OdsCell:
    text: str
    value: Decimal | None = None
    formula: str | None = None


@dataclass
class OdsRow:
    cells: list[OdsCell]

    def text_at(self, col: int) -> str:
        return self.cells[col].text if col < len(self.cells) else ""

    def value_at(self, col: int) -> Decimal | None:
        return self.cells[col].value if col < len(self.cells) else None


@dataclass
class OfferBlock:
    soll_header: str
    rows_positions: list[OdsRow] = field(default_factory=list)
    row_sum: OdsRow | None = None


def _parse_cell(elem: ET.Element) -> OdsCell:
    text_parts: list[str] = []
    for p in elem.iter(NS_P + "p"):
        text_parts.append("".join(p.itertext()))
    text = "\n".join(t for t in text_parts if t).strip()
    formula = elem.get(NS_T + "formula")
    value_attr = elem.get(NS_OFFICE + "value")
    value: Decimal | None = None
    if value_attr is not None:
        try:
            value = Decimal(value_attr)
        except InvalidOperation:
            value = None
    return OdsCell(text=text, value=value, formula=formula)


def _expand_row(row_elem: ET.Element) -> OdsRow:
    cells: list[OdsCell] = []
    for cell_elem in row_elem.findall(NS_T + "table-cell"):
        cell = _parse_cell(cell_elem)
        repeat = int(cell_elem.get(NS_T + "number-columns-repeated", "1"))
        for _ in range(repeat):
            cells.append(cell)
    return OdsRow(cells=cells)


def read_kostenaufstellung(path: Path) -> list[OfferBlock]:
    """Read an .ods and return a list of OfferBlock, one per vendor block detected by SOLL header."""
    with zipfile.ZipFile(path) as z:
        content = z.read("content.xml")
    root = ET.fromstring(content)

    blocks: list[OfferBlock] = []
    current: OfferBlock | None = None

    # Only the first sheet is the Kostenaufstellung cost table; later sheets (e.g.
    # Kostenbeschreibung) reuse the SOLL header rows for prose and must not be parsed as
    # cost blocks.
    tables = list(root.iter(NS_T + "table"))
    for table in tables[:1]:
        for row_elem in table.iter(NS_T + "table-row"):
            row = _expand_row(row_elem)
            first_text = row.text_at(0)

            if first_text and SOLL_HEADER_RE.match(first_text):
                if current is not None:
                    blocks.append(current)
                current = OfferBlock(soll_header=first_text)
                continue

            if current is None:
                continue

            if first_text.lower().startswith("position") or first_text == "Position":
                continue

            if first_text.startswith(("Σ", "Sum", "Gesamtpreis")):
                current.row_sum = row
                continue

            # A position row always carries a pos label in column 0. Numeric labels
            # ("1", "1-9") and non-numeric optional labels ("E1", "O1" for Eventual-/
            # Optionalpositionen) both count. The Sonderpreis and percentage rows leave
            # column 0 empty, so an empty first cell is never a position.
            if first_text:
                current.rows_positions.append(row)

    if current is not None:
        blocks.append(current)

    return blocks
