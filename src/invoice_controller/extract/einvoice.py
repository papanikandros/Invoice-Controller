"""Embedded e-invoice tier (ZUGFeRD / Factur-X / XRechnung-in-PDF) — tier 0, before OCR/LLM.

German e-invoicing PDFs carry the invoice as machine-readable CII XML in an embedded
file. When present, parsing it yields EXACT header amounts and line items with zero
extraction risk — the cross-sum then validates the vendor's own XML (the EN 16931
BR-CO rules define the same sums), not our extraction. The corpus already contains
such invoices (2026-08-31 scan: 9 of 348 PDFs) and the share will grow as the German
B2B e-invoicing mandates phase in.

Parsing is deliberately namespace-agnostic (local-name matching): ZUGFeRD 1.0
(`CrossIndustryDocument`), ZUGFeRD 2.x / Factur-X (`CrossIndustryInvoice`, CII D16B)
and the profile dialects differ only in namespaces and a few wrapper names for the
handful of fields we need. Dedicated libraries (drafthorse) were evaluated and
rejected: strict schema binding failed on 2 of 3 real corpus e-invoices.

Anything the XML does not state stays None — same honesty rule as the LLM path.
A parse failure falls back to the normal text/OCR/vision chain (loudly, via the
amount-check message)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pypdf import PdfReader

from invoice_controller.llm.extract_invoice import ExtractedInvoice
from invoice_controller.models import InvoicePosition, InvoiceType, VatStatus

# Known embedded-file names across profile versions; any other *.xml attachment is
# tried too (some producers use the invoice number as file name).
_KNOWN_NAMES = {"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "order-x.xml"}
_ROOT_LOCALNAMES = {"CrossIndustryInvoice", "CrossIndustryDocument"}

# UNTDID 1001 document type codes → our invoice types. Codes we do not map fall
# back to RECHNUNG — the type only steers labels/checks, never amounts.
_TYPE_CODES = {
    "380": InvoiceType.RECHNUNG,
    "381": InvoiceType.GUTSCHRIFT,
    "384": InvoiceType.RECHNUNG,       # korrigierte Rechnung
    "326": InvoiceType.TEILRECHNUNG,
    "389": InvoiceType.RECHNUNG,       # Selbstfakturierung
}


class EInvoiceParseError(Exception):
    """Embedded XML exists but the essential fields could not be recovered."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(elem: ET.Element, *localnames: str) -> ET.Element | None:
    for e in elem.iter():
        if _local(e.tag) in localnames:
            return e
    return None


def _findall(elem: ET.Element, localname: str) -> list[ET.Element]:
    return [e for e in elem.iter() if _local(e.tag) == localname]


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    t = elem.text.strip()
    return t or None


def _dec(elem: ET.Element | None) -> Decimal | None:
    t = _text(elem)
    if t is None:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _parse_date(elem: ET.Element | None) -> date | None:
    """CII dates come as <udt:DateTimeString format="102">YYYYMMDD</> or ISO."""
    t = _text(_find(elem, "DateTimeString") if elem is not None else None)
    if t is None:
        return None
    digits = t.replace("-", "")[:8]
    if len(digits) == 8 and digits.isdigit():
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def find_embedded_invoice_xml(path: Path) -> bytes | None:
    """Return the embedded CII invoice XML, or None when the PDF carries none."""
    try:
        attachments = PdfReader(str(path)).attachments
    except Exception:
        return None
    candidates: list[tuple[int, bytes]] = []
    for name, contents in attachments.items():
        base = name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower()
        if not base.endswith(".xml"):
            continue
        rank = 0 if base in _KNOWN_NAMES else 1
        for blob in contents:
            candidates.append((rank, blob))
    for _, blob in sorted(candidates, key=lambda c: c[0]):
        try:
            root = ET.fromstring(blob)
        except ET.ParseError:
            continue
        if _local(root.tag) in _ROOT_LOCALNAMES:
            return blob
    return None


def _address(party: ET.Element | None) -> str | None:
    if party is None:
        return None
    postal = _find(party, "PostalTradeAddress")
    if postal is None:
        return None
    parts = [
        _text(_find(postal, "LineOne")),
        " ".join(
            p for p in (_text(_find(postal, "PostcodeCode")), _text(_find(postal, "CityName"))) if p
        )
        or None,
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_cii_invoice(xml: bytes) -> ExtractedInvoice:
    """Map the CII XML onto the shared ExtractedInvoice shape. Only fields the XML
    states are filled; the essential trio (vendor, number, netto) must be present."""
    root = ET.fromstring(xml)

    header = _find(root, "ExchangedDocument", "HeaderExchangedDocument")
    if header is None:
        raise EInvoiceParseError("kein ExchangedDocument-Element")
    invoice_number = _text(_find(header, "ID"))
    invoice_date = _parse_date(_find(header, "IssueDateTime"))
    type_code = _text(_find(header, "TypeCode")) or "380"

    seller = _find(root, "SellerTradeParty")
    buyer = _find(root, "BuyerTradeParty")
    vendor_name = _text(_find(seller, "Name")) if seller is not None else None

    # Header monetary summation: ZUGFeRD 1.0 and CII 100 use different wrapper names;
    # both differ from the per-line summation, so local-name matching is unambiguous.
    summation = _find(
        root,
        "SpecifiedTradeSettlementHeaderMonetarySummation",
        "SpecifiedTradeSettlementMonetarySummation",
    )
    if summation is None:
        raise EInvoiceParseError("keine Monetary Summation")
    line_total = _dec(_find(summation, "LineTotalAmount"))
    netto = _dec(_find(summation, "TaxBasisTotalAmount")) or line_total
    mwst_amount = _dec(_find(summation, "TaxTotalAmount"))
    brutto = _dec(_find(summation, "GrandTotalAmount"))

    if vendor_name is None or invoice_number is None or invoice_date is None or netto is None:
        raise EInvoiceParseError(
            "Pflichtfelder fehlen im XML (Verkäufer / Rechnungsnummer / Datum / Netto)"
        )

    # Elements inside line items, so header-level allowances/taxes can be told apart.
    line_items = _findall(root, "IncludedSupplyChainTradeLineItem")
    in_lines: set[int] = set()
    for li in line_items:
        for e in li.iter():
            in_lines.add(id(e))

    mwst_pct = None
    for tax in _findall(root, "ApplicableTradeTax"):
        if id(tax) in in_lines:
            continue
        mwst_pct = _dec(_find(tax, "RateApplicablePercent", "ApplicablePercent"))
        if mwst_pct is not None:
            break

    if mwst_pct == Decimal(19):
        vat_status = VatStatus.STANDARD
    elif mwst_pct == Decimal(7):
        vat_status = VatStatus.REDUCED
    elif mwst_amount is not None and mwst_amount == 0 and brutto == netto:
        vat_status = VatStatus.TAX_FREE
    else:
        vat_status = VatStatus.UNCLEAR

    positions: list[InvoicePosition] = []
    for li in line_items:
        li_sum = _find(
            li,
            "SpecifiedTradeSettlementLineMonetarySummation",
            "SpecifiedTradeSettlementMonetarySummation",
        )
        total = _dec(_find(li_sum, "LineTotalAmount")) if li_sum is not None else None
        if total is None:
            continue
        qty_elem = _find(li, "BilledQuantity")
        positions.append(
            InvoicePosition(
                pos=_text(_find(li, "LineID")) or "",
                description=_text(_find(li, "Name")) or "(ohne Bezeichnung)",
                qty=_dec(qty_elem),
                unit=(qty_elem.get("unitCode") if qty_elem is not None else None),
                line_total_net=total,
            )
        )

    # Header-level document discounts/charges are stated XML facts; carried as
    # positions so Σ(positions) reconciles with TaxBasisTotal, mirroring how the
    # LLM path keeps genuine Rabatt lines.
    explicit_net = Decimal(0)
    for ac in _findall(root, "SpecifiedTradeAllowanceCharge"):
        if id(ac) in in_lines:
            continue
        amount = _dec(_find(ac, "ActualAmount"))
        if amount is None or amount == 0:
            continue
        indicator = (_text(_find(ac, "Indicator")) or "false").lower()
        is_charge = indicator in ("true", "1")
        signed = amount if is_charge else -amount
        explicit_net += signed
        positions.append(
            InvoicePosition(
                pos="",
                description=_text(_find(ac, "Reason"))
                or ("Zuschlag lt. E-Rechnung" if is_charge else "Nachlass lt. E-Rechnung"),
                line_total_net=signed,
            )
        )

    # Some producers (ZUGFeRD 1.0 corpus cases) state the discount ONLY as the
    # summation's AllowanceTotalAmount, without a SpecifiedTradeAllowanceCharge
    # detail element. Carry the remainder not already covered above.
    summation_net = (_dec(_find(summation, "ChargeTotalAmount")) or Decimal(0)) - (
        _dec(_find(summation, "AllowanceTotalAmount")) or Decimal(0)
    )
    remainder = summation_net - explicit_net
    if abs(remainder) > Decimal("0.005"):
        positions.append(
            InvoicePosition(
                pos="",
                description="Nachlass lt. E-Rechnung"
                if remainder < 0
                else "Zuschlag lt. E-Rechnung",
                line_total_net=remainder,
            )
        )

    return ExtractedInvoice(
        vendor_name=vendor_name,
        vendor_address=_address(seller),
        recipient_name=_text(_find(buyer, "Name")) if buyer is not None else None,
        recipient_address=_address(buyer),
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        invoice_type=_TYPE_CODES.get(type_code, InvoiceType.RECHNUNG),
        netto=netto,
        mwst_pct=mwst_pct,
        mwst_amount=mwst_amount,
        brutto=brutto,
        vat_status=vat_status,
        positions=positions,
    )
