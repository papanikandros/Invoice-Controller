"""E-invoice tier (R1) + verbatim-amount grounding (R2) + bounded retry (R5).

The CII fixture is a handcrafted minimal Factur-X profile — both namespace dialects
(ZUGFeRD 1.0 CrossIndustryDocument and CII-100 CrossIndustryInvoice) exercise the
namespace-agnostic reader. The corpus-backed case (Zacharias) auto-skips when
`examples/` is absent.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from invoice_controller.extract.cross_sum import check_invoice_positions
from invoice_controller.extract.einvoice import (
    EInvoiceParseError,
    find_embedded_invoice_xml,
    parse_cii_invoice,
)
from invoice_controller.extract.grounding import check_amounts_grounded
from invoice_controller.models import InvoiceType, VatStatus

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

_CII = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>RE-2026-001</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime><udt:DateTimeString format="102">20260315</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>1</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>Wärmepumpe Montage</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="H87">2</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>800.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>2</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>Kleinmaterial</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeSettlement>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>200.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty>
        <ram:Name>Muster Haustechnik GmbH</ram:Name>
        <ram:PostalTradeAddress>
          <ram:PostcodeCode>48143</ram:PostcodeCode>
          <ram:LineOne>Musterweg 1</ram:LineOne>
          <ram:CityName>Münster</ram:CityName>
        </ram:PostalTradeAddress>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty><ram:Name>Max Mustermann</ram:Name></ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:ApplicableTradeTax><ram:RateApplicablePercent>19</ram:RateApplicablePercent></ram:ApplicableTradeTax>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>1000.00</ram:LineTotalAmount>
        <ram:AllowanceTotalAmount>50.00</ram:AllowanceTotalAmount>
        <ram:TaxBasisTotalAmount>950.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount>180.50</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>1130.50</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


def _pdf_with_attachment(tmp_path: Path, name: str, xml: bytes) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_attachment(name, xml)
    out = tmp_path / "einvoice.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


class TestFindEmbeddedXml:
    def test_finds_known_name(self, tmp_path: Path) -> None:
        pdf = _pdf_with_attachment(tmp_path, "factur-x.xml", _CII.encode())
        assert find_embedded_invoice_xml(pdf) is not None

    def test_ignores_non_invoice_xml(self, tmp_path: Path) -> None:
        pdf = _pdf_with_attachment(tmp_path, "metadata.xml", b"<other/>")
        assert find_embedded_invoice_xml(pdf) is None

    def test_plain_pdf_returns_none(self, tmp_path: Path) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        out = tmp_path / "plain.pdf"
        with out.open("wb") as fh:
            writer.write(fh)
        assert find_embedded_invoice_xml(out) is None


class TestParseCii:
    def test_header_fields(self) -> None:
        inv = parse_cii_invoice(_CII.encode())
        assert inv.vendor_name == "Muster Haustechnik GmbH"
        assert inv.vendor_address == "Musterweg 1, 48143 Münster"
        assert inv.recipient_name == "Max Mustermann"
        assert inv.invoice_number == "RE-2026-001"
        assert inv.invoice_date.isoformat() == "2026-03-15"
        assert inv.invoice_type is InvoiceType.RECHNUNG
        assert inv.netto == Decimal("950.00")
        assert inv.mwst_pct == Decimal("19")
        assert inv.mwst_amount == Decimal("180.50")
        assert inv.brutto == Decimal("1130.50")
        assert inv.vat_status is VatStatus.STANDARD

    def test_positions_include_summation_allowance(self) -> None:
        """The 50-€ discount exists only as AllowanceTotalAmount (the ZUGFeRD 1.0
        corpus pattern) — it must become a negative position so Σ reconciles."""
        inv = parse_cii_invoice(_CII.encode())
        assert [str(p.line_total_net) for p in inv.positions] == ["800.00", "200.00", "-50.00"]
        assert inv.positions[0].qty == Decimal("2")
        assert inv.positions[0].unit == "H87"
        assert inv.positions[2].description == "Nachlass lt. E-Rechnung"
        assert check_invoice_positions(inv.positions, inv.netto).passed

    def test_zugferd1_namespace_dialect(self) -> None:
        old = (
            _CII.replace("CrossIndustryInvoice:100", "CrossIndustryDocument:invoice:1p0")
            .replace("rsm:CrossIndustryInvoice", "rsm:CrossIndustryDocument")
            .replace("ExchangedDocument", "HeaderExchangedDocument")
            .replace(
                "SpecifiedTradeSettlementHeaderMonetarySummation",
                "SpecifiedTradeSettlementMonetarySummation",
            )
        )
        inv = parse_cii_invoice(old.encode())
        assert inv.netto == Decimal("950.00")
        assert len(inv.positions) == 3

    def test_missing_essentials_raise(self) -> None:
        broken = _CII.replace("<ram:ID>RE-2026-001</ram:ID>", "")
        with pytest.raises(EInvoiceParseError):
            parse_cii_invoice(broken.encode())


@pytest.mark.skipif(not EXAMPLES.exists(), reason="examples corpus not present")
class TestCorpusEInvoice:
    def test_zacharias_matches_documented_ground_truth(self) -> None:
        pdf = EXAMPLES / "BEG/EM_Aepker/EM/Rechnungen/R043404_Rechnung_Zacharias.pdf"
        if not pdf.exists():
            pytest.skip("Zacharias corpus file absent")
        xml = find_embedded_invoice_xml(pdf)
        assert xml is not None
        inv = parse_cii_invoice(xml)
        # Figures documented in PLAN.md from the consultant's sheet.
        assert inv.netto == Decimal("24876.25")
        assert inv.brutto == Decimal("29602.74")
        assert check_invoice_positions(inv.positions, inv.netto).passed


class TestGrounding:
    TEXT = "Pos 1 Montage 1.234,56\nNettosumme 1.234,56\nMwSt 19% 234,57\nGesamt 1.469,13"

    def test_all_found(self) -> None:
        check = check_amounts_grounded(
            {"Netto": Decimal("1234.56"), "MwSt": Decimal("234.57"), "Brutto": Decimal("1469.13")},
            self.TEXT,
        )
        assert check.passed

    def test_missing_amount_flagged_by_label(self) -> None:
        check = check_amounts_grounded({"Netto": Decimal("9999.99")}, self.TEXT)
        assert not check.passed
        assert "Netto" in (check.message or "")

    def test_none_values_skipped(self) -> None:
        assert check_amounts_grounded({"Brutto": None}, "").passed

    def test_negative_amount_matches_unsigned_print(self) -> None:
        assert check_amounts_grounded({"Gutschrift": Decimal("-1234.56")}, self.TEXT).passed

    def test_ungrouped_variant_matches(self) -> None:
        assert check_amounts_grounded({"Netto": Decimal("1234.56")}, "Summe 1234,56").passed


# --- R5: bounded retry on failed position cross-sum ------------------------------------

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from invoice_controller.extract import invoice as invoice_mod
from invoice_controller.llm.extract_invoice import SYSTEM_PROMPT, ExtractedInvoice

_BASE = {
    "vendor_name": "Muster GmbH",
    "invoice_number": "R-1",
    "invoice_date": "2026-03-01",
    "netto": "1000.00",
    "mwst_pct": "19",
    "mwst_amount": "190.00",
    "brutto": "1190.00",
}
_BAD_POSITIONS = [{"pos": "1", "description": "Montage", "line_total_net": "900.00"}]
_GOOD_POSITIONS = [{"pos": "1", "description": "Montage", "line_total_net": "1000.00"}]


def _counting_agent(payloads: list[dict]) -> tuple[Agent, list[int]]:
    """Returns payloads[i] for call i (last one repeats); records the call count."""
    calls = [0]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        payload = payloads[min(calls[0], len(payloads) - 1)]
        calls[0] += 1
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=payload)])

    return Agent(FunctionModel(respond), output_type=ExtractedInvoice, system_prompt=SYSTEM_PROMPT), calls


class TestBoundedRetry:
    def _run(self, monkeypatch, payloads, **kwargs):
        agent, calls = _counting_agent(payloads)
        monkeypatch.setattr(invoice_mod, "extract_pages", lambda _: ["Rechnung 1.000,00 Text"])
        monkeypatch.setattr(invoice_mod, "find_embedded_invoice_xml", lambda _: None)
        doc = invoice_mod.extract_invoice(Path("fake.pdf"), agent=agent, **kwargs)
        return doc, calls[0]

    def test_retry_adopted_when_it_reconciles(self, monkeypatch) -> None:
        doc, n = self._run(
            monkeypatch,
            [dict(_BASE, positions=_BAD_POSITIONS), dict(_BASE, positions=_GOOD_POSITIONS)],
        )
        assert n == 2
        assert doc.position_check is not None and doc.position_check.passed
        assert doc.extraction_method.endswith("+retry")

    def test_failed_retry_keeps_deterministic_run(self, monkeypatch) -> None:
        doc, n = self._run(monkeypatch, [dict(_BASE, positions=_BAD_POSITIONS)])
        assert n == 2
        assert doc.position_check is not None and not doc.position_check.passed
        assert "+retry" not in doc.extraction_method
        assert doc.positions[0].line_total_net == Decimal("900.00")

    def test_no_retry_when_disabled(self, monkeypatch) -> None:
        doc, n = self._run(monkeypatch, [dict(_BASE, positions=_BAD_POSITIONS)], with_retry=False)
        assert n == 1
        assert not doc.position_check.passed

    def test_no_retry_when_first_run_passes(self, monkeypatch) -> None:
        doc, n = self._run(monkeypatch, [dict(_BASE, positions=_GOOD_POSITIONS)])
        assert n == 1
        assert "+retry" not in doc.extraction_method
