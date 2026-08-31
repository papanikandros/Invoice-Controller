"""Per-vendor IK/NK ratios — the `Anteil` column's single source of truth.

The Anteil applied to every invoice of a vendor IS that vendor's cost-estimation
Kostenaufstellung percentage row (verified against the close-out corpus: the
consultant's sheets reference it live via `'[1](INTEREN Kostenaufstellung)'!D…/E…`).
No override mechanism exists by design — a deviation is the consultant's manual
edit in the generated result file.

Three sources, in priority order (`load_vendor_ratios`):

  1. `Kostenaufstellung.ods` in the project folder — the cost-estimation output, which the
     consultant has verified and possibly corrected. Free and deterministic.
  2. A consultant-built `Kostenaufstellung*.pdf` (the corpus projects ship these).
  3. Live cost-estimation offer extraction (costs LLM calls) — the caller passes OfferDocuments.

Anteile are renormalized over IK+NK (`ik / (ik + nk)`): an cost-estimation block with a
Nachlass share would otherwise leave part of every invoice netto unallocated.
"""

from __future__ import annotations

import re
import subprocess
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from invoice_controller.models import Kostenkategorie, OfferDocument

_NS_T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_NS_P = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_NS_O = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"

# German money / percent tokens as printed in the consultant PDFs.
_MONEY = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}")
_PCT = re.compile(r"-?\d{1,3}(?:\.\d{3})*,\d{2}\s*%")
_BLOCK_HEADER = re.compile(
    r"(\b(SOLL|NEU)\b.*(Angebot|vom|Sch[äa]tzung|Nebenkosten|Nebenosten))"
    r"|(Sch[äa]tzung|Stellungnahme)",
    re.IGNORECASE,
)
_SUM_LINE = re.compile(
    r"Gesamtpreis|Summe|Angebotspreis|Nettosumme|Gesamtsumme|Endbetrag", re.IGNORECASE
)
_SONDER = re.compile(r"Sonderpreis|Sonderkonditionen|Pauschalpreis", re.IGNORECASE)


def _de_money(tok: str) -> Decimal:
    return Decimal(tok.replace(".", "").replace(",", "."))


def _de_pct(tok: str) -> Decimal:
    return Decimal(tok.replace("%", "").strip().replace(".", "").replace(",", "."))


@dataclass
class VendorRatio:
    vendor: str                      # display label, e.g. "MAFAC" or the block header rest
    header: str                      # full SOLL header for reference/audit
    anteil_ik: Decimal               # renormalized: ik / (ik + nk)
    anteil_nk: Decimal
    beantragt_ik: Decimal | None     # the cost-estimation block's IK sum (feeds 'beantragt IK')
    beantragt_nk: Decimal | None
    gesamt: Decimal | None           # cost-estimation block Σ — for per-vendor reconciliation
    sonderpreis: Decimal | None
    source: str                      # "ods" | "pdf" | "live-extraction"


def _renormalize(ik: Decimal, nk: Decimal) -> tuple[Decimal, Decimal]:
    base = ik + nk
    if base == 0:
        return Decimal(0), Decimal(0)
    return ik / base, nk / base


_VENDOR_FROM_HEADER = re.compile(
    r"^(?:SOLL|NEU)\s*:?\s*(?:SCHÄTZUNG\s+lt\.\s+Stellungnahme\s+)?(?P<vendor>.+?)"
    r"\s+(?:Angebot|Stellungnahme|vom\b)",
    re.IGNORECASE,
)
# Some headers put the vendor AFTER the date: "SOLL Angebot.Nr: <nr> vom
# 24.04.2023 Fa. RÖCHER GmbH & Co. KG" (Jacob).
_VENDOR_AFTER_DATE = re.compile(
    r"vom\s+\d{1,2}\.\d{1,2}\.\d{2,4}[,;]?\s+(?:Fa\.\s*)?(?P<vendor>[^,;]+)",
    re.IGNORECASE,
)


def vendor_from_header(header: str) -> str:
    header = header.strip()
    m = _VENDOR_FROM_HEADER.search(header)
    vendor = m.group("vendor").strip(" ,;:") if m else ""
    if not vendor or vendor.lower().startswith("angebot"):
        after = _VENDOR_AFTER_DATE.search(header)
        if after:
            return after.group("vendor").strip(" ,;:")
    if vendor:
        return vendor
    # Statement-style headers without a vendor slot: fall back to the full header.
    return header


# ---------------------------------------------------------------------------
# Source 1 — the cost-estimation Kostenaufstellung .ods (possibly consultant-corrected)
# ---------------------------------------------------------------------------

def _cell_texts_and_values(row_elem: ET.Element) -> tuple[list[str], list[Decimal | None]]:
    texts: list[str] = []
    values: list[Decimal | None] = []
    for cell in row_elem.findall(_NS_T + "table-cell"):
        text = "\n".join("".join(p.itertext()) for p in cell.iter(_NS_P + "p")).strip()
        vattr = cell.get(_NS_O + "value")
        value: Decimal | None = None
        if vattr is not None:
            try:
                value = Decimal(vattr)
            except Exception:
                value = None
        repeat = int(cell.get(_NS_T + "number-columns-repeated", "1"))
        for _ in range(repeat):
            texts.append(text)
            values.append(value)
    return texts, values


def from_kostenaufstellung_ods(path: Path) -> list[VendorRatio]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    tables = list(root.iter(_NS_T + "table"))
    ratios: list[VendorRatio] = []
    header: str | None = None
    sums: list[Decimal | None] | None = None
    sonderpreis: Decimal | None = None

    def close_block() -> None:
        nonlocal header, sums, sonderpreis
        if header is None or sums is None:
            header, sums, sonderpreis = None, None, None
            return
        gesamt, ik, nk = sums[2], sums[3], sums[4]
        if gesamt is not None and ik is not None and nk is not None:
            anteil_ik, anteil_nk = _renormalize(ik, nk)
            ratios.append(
                VendorRatio(
                    vendor=vendor_from_header(header),
                    header=header,
                    anteil_ik=anteil_ik,
                    anteil_nk=anteil_nk,
                    beantragt_ik=ik,
                    beantragt_nk=nk,
                    gesamt=gesamt,
                    sonderpreis=sonderpreis,
                    source="ods",
                )
            )
        header, sums, sonderpreis = None, None, None

    for table in tables[:1]:  # first sheet only — later sheets reuse SOLL headers for prose
        for row_elem in table.iter(_NS_T + "table-row"):
            texts, values = _cell_texts_and_values(row_elem)
            first = texts[0] if texts else ""
            if first.startswith("SOLL"):
                close_block()
                header = first
            elif header is not None and first.startswith("Σ"):
                sums = values
            elif header is not None and len(texts) > 1 and texts[1].startswith("Sonderpreis"):
                sonderpreis = values[2] if len(values) > 2 else None
        close_block()
    return ratios


def from_kostenaufstellung_xlsx(path: Path) -> list[VendorRatio]:
    """Source 1 in the .xlsx era (format decision 2026-08-28): the cost-estimation output is an
    .xlsx whose Σ row holds live formulas WITHOUT cached values, so the block sums are
    recomputed from the position rows. A consultant-saved copy (recalculated by
    Excel/LibreOffice) may carry cached Σ values — those win when present, because the
    consultant may have corrected position rows and the Σ row is the verified figure."""
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]

    ratios: list[VendorRatio] = []
    header: str | None = None
    pos_sums: list[Decimal] | None = None      # [gesamt, ik, nk] accumulated from rows
    sum_row_vals: list[Decimal | None] | None = None
    sonderpreis: Decimal | None = None

    def _dec(v) -> Decimal | None:
        if v is None or isinstance(v, str):
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    def close_block() -> None:
        nonlocal header, pos_sums, sum_row_vals, sonderpreis
        if header is not None:
            figures: tuple[Decimal, Decimal, Decimal] | None = None
            if sum_row_vals is not None and all(v is not None for v in sum_row_vals):
                figures = tuple(sum_row_vals)  # type: ignore[assignment]
            elif pos_sums is not None:
                figures = tuple(pos_sums)  # type: ignore[assignment]
            if figures is not None:
                gesamt, ik, nk = figures
                anteil_ik, anteil_nk = _renormalize(ik, nk)
                ratios.append(
                    VendorRatio(
                        vendor=vendor_from_header(header),
                        header=header,
                        anteil_ik=anteil_ik,
                        anteil_nk=anteil_nk,
                        beantragt_ik=ik,
                        beantragt_nk=nk,
                        gesamt=gesamt,
                        sonderpreis=sonderpreis,
                        source="xlsx",
                    )
                )
        header, pos_sums, sum_row_vals, sonderpreis = None, None, None, None

    for row in ws.iter_rows(min_col=1, max_col=6):
        a = row[0].value
        b = row[1].value if len(row) > 1 else None
        first = a.strip() if isinstance(a, str) else ""
        if first.startswith("SOLL"):
            close_block()
            header = first
            pos_sums = [Decimal(0), Decimal(0), Decimal(0)]
            continue
        if header is None:
            continue
        if first.startswith("Σ"):
            sum_row_vals = [_dec(row[2].value), _dec(row[3].value), _dec(row[4].value)]
            continue
        if isinstance(b, str) and b.startswith("Sonderpreis"):
            sonderpreis = _dec(row[2].value)
            continue
        if isinstance(b, str) and b.startswith("Nettosumme lt. Dokument"):
            continue  # cross-sum comparison row, not a position
        if first and first != "Position" and pos_sums is not None and sum_row_vals is None:
            g, ik, nk = _dec(row[2].value), _dec(row[3].value), _dec(row[4].value)
            if g is not None:
                pos_sums[0] += g
                pos_sums[1] += ik or Decimal(0)
                pos_sums[2] += nk or Decimal(0)
    close_block()
    return ratios


# ---------------------------------------------------------------------------
# Source 2 — a consultant-built Kostenaufstellung PDF (corpus projects)
# ---------------------------------------------------------------------------

_RATIO_AGREEMENT = Decimal("0.002")


def choose_block_figures(
    totals: list[Decimal],
    sonder_totals: list[Decimal],
    pcts: list[Decimal],
) -> tuple[Decimal, Decimal, Decimal, Decimal | None] | None:
    """Pick (gesamt, ik, nk, sonderpreis) for one SOLL block from the rows the PDF
    offers. Candidates in priority order — the Sonderpreis row is the binding
    (discounted) figure when it carries a per-category split (KRR), the Σ money
    row gives cent precision (ZePa) — but a money-derived ratio is only trusted
    when it AGREES with the printed % row (the canonical cost-estimation ratio row): legacy
    layouts exist where the money columns don't carry scaled IK/NK (WHW), and
    there the % row wins."""
    pct_ratio: Decimal | None = None
    if len(pcts) >= 3 and (pcts[1] + pcts[2]) > 0:
        pct_ratio = pcts[1] / (pcts[1] + pcts[2])

    def agrees(ik: Decimal, nk: Decimal) -> bool:
        if pct_ratio is None:
            return True
        base = ik + nk
        if base <= 0:
            return False
        return abs(ik / base - pct_ratio) <= _RATIO_AGREEMENT

    sonderpreis = sonder_totals[0] if sonder_totals else None
    for candidate in (sonder_totals, totals):
        if len(candidate) >= 3 and candidate[0] > 0:
            gesamt, ik, nk = candidate[0], candidate[1], candidate[2]
            # Internal consistency: the split must reproduce the row's own total.
            if abs((ik + nk) - gesamt) <= Decimal("0.05") and agrees(ik, nk):
                return gesamt, ik, nk, sonderpreis

    if totals and pct_ratio is not None:
        gesamt = totals[0]
        ik = (gesamt * pct_ratio).quantize(Decimal("0.01"))
        return gesamt, ik, gesamt - ik, sonderpreis
    if totals:
        return totals[0], totals[0], Decimal(0), sonderpreis
    return None


def from_kostenaufstellung_pdf(path: Path) -> list[VendorRatio]:
    out = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    )
    ratios: list[VendorRatio] = []
    header: str | None = None
    totals: list[Decimal] = []
    sonder_totals: list[Decimal] = []
    pcts: list[Decimal] = []
    pending: list[Decimal] = []

    def close_block() -> None:
        nonlocal header, totals, sonder_totals, pcts, pending
        if header is not None:
            chosen = choose_block_figures(totals, sonder_totals, pcts)
            if chosen is not None:
                gesamt, ik, nk, sonderpreis = chosen
                anteil_ik, anteil_nk = _renormalize(ik, nk)
                ratios.append(
                    VendorRatio(
                        vendor=vendor_from_header(header),
                        header=header,
                        anteil_ik=anteil_ik,
                        anteil_nk=anteil_nk,
                        beantragt_ik=ik.quantize(Decimal("0.01")),
                        beantragt_nk=nk.quantize(Decimal("0.01")),
                        gesamt=gesamt,
                        sonderpreis=sonderpreis,
                        source="pdf",
                    )
                )
        header, totals, sonder_totals, pcts, pending = None, [], [], [], []

    for line in out.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_position = bool(re.match(r"^\s*\d+[.\d]*\s+\S", line))
        if not is_position and _BLOCK_HEADER.search(stripped):
            close_block()
            header = stripped
            continue
        if header is None:
            continue
        if _SONDER.search(stripped):
            monies = _MONEY.findall(stripped)
            if monies:
                sonder_totals = [_de_money(m) for m in monies]
            continue
        found_pcts = _PCT.findall(stripped)
        if len(found_pcts) >= 2 and not pcts:
            pcts = [_de_pct(p) for p in found_pcts]
            if not totals and pending:
                totals = pending
            continue
        monies = _MONEY.findall(stripped)
        if not is_position and _SUM_LINE.search(stripped) and len(monies) >= 2:
            totals = [_de_money(m) for m in monies]
            pending = totals
            continue
        if not is_position and len(monies) >= 2:
            pending = [_de_money(m) for m in monies]
    close_block()
    return ratios


# ---------------------------------------------------------------------------
# Source 3 — in-memory cost-estimation results (live extraction)
# ---------------------------------------------------------------------------

def from_offer_documents(offers: list[OfferDocument]) -> list[VendorRatio]:
    ratios: list[VendorRatio] = []
    for offer in offers:
        ik = nk = gesamt = Decimal(0)
        for p in offer.positions:
            if p.line_total_net is None:
                continue
            gesamt += p.line_total_net
            if p.kategorie is Kostenkategorie.INVESTITIONSKOSTEN:
                ik += p.line_total_net
            elif p.kategorie is Kostenkategorie.NEBENKOSTEN:
                nk += p.line_total_net
        anteil_ik, anteil_nk = _renormalize(ik, nk)
        ratios.append(
            VendorRatio(
                vendor=offer.header.vendor_short or offer.header.vendor_name,
                header=offer.header.vendor_name,
                anteil_ik=anteil_ik,
                anteil_nk=anteil_nk,
                beantragt_ik=ik,
                beantragt_nk=nk,
                gesamt=gesamt,
                sonderpreis=offer.totals.sonderpreis,
                source="live-extraction",
            )
        )
    return ratios


_STATEMENT_BLOCK_RE = re.compile(r"sch[äa]tzung|stellungnahme|eigenerkl", re.IGNORECASE)


def is_statement_block(ratio: VendorRatio) -> bool:
    """A SCHÄTZUNG/Stellungnahme block estimates costs for which no vendor offer
    existed at application time — it has no vendor name to match invoices against,
    but its ratio (typically 100 % NK) is the offer-side split for exactly the
    invoices that later arrive without an offer."""
    return bool(_STATEMENT_BLOCK_RE.search(ratio.header))


def find_kostenaufstellung_pdf(project_dir: Path) -> Path | None:
    """The cost-estimation-format PDF is the one carrying the IK/NK percentage split rows;
    raw vendor offers with 'Kostenaufstellung' in the name lack them."""
    for p in sorted(project_dir.glob("*.pdf")):
        if "kostenaufstellung" not in p.name.lower():
            continue
        try:
            if from_kostenaufstellung_pdf(p):
                return p
        except Exception:
            continue
    return None


def load_vendor_ratios(project_dir: Path) -> tuple[list[VendorRatio], str]:
    """Resolve the best available ratio source for a project folder. Returns
    (ratios, source description). Empty list when neither a Kostenaufstellung
    (.xlsx/.ods) nor an cost-estimation-format PDF exists — the caller then runs live cost-estimation
    extraction or red-flags everything. The .xlsx (current cost-estimation output format) is
    preferred; the .ods tier remains for projects generated before 2026-08-28."""
    xlsx = project_dir / "Kostenaufstellung.xlsx"
    if xlsx.exists():
        ratios = from_kostenaufstellung_xlsx(xlsx)
        if ratios:
            return ratios, f"xlsx:{xlsx.name}"
    ods = project_dir / "Kostenaufstellung.ods"
    if ods.exists():
        ratios = from_kostenaufstellung_ods(ods)
        if ratios:
            return ratios, f"ods:{ods.name}"
    pdf = find_kostenaufstellung_pdf(project_dir)
    if pdf is not None:
        ratios = from_kostenaufstellung_pdf(pdf)
        if ratios:
            return ratios, f"pdf:{pdf.name}"
    return [], "none"
