"""E2 — cite-then-check: the verify turn confirms or leaves doubtful fields unresolved."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from invoice_controller.extract import invoice as invoice_mod
from invoice_controller.extract.verify import VerifyOutput
from invoice_controller.llm.extract_invoice import SYSTEM_PROMPT, ExtractedInvoice

PAGE = "Rudi Sönnecken\nRg.Nr. 705174\nDatum: 09.09.2026\nNetto 954,73 €\nMwSt 181,39 €\nBrutto 1.136,12 €"

_BASE = dict(
    vendor_name="Rudi Sönnecken", vendor_name_quote="Rudi Sönnecken",
    invoice_number="705174", invoice_number_quote="Rg.Nr. 705174",
    invoice_date="2024-04-23", invoice_date_quote="A.04.2024",          # misread: value not in quote
    netto="954.73", netto_quote="Netto 954,73 €", mwst_pct="19", mwst_amount="181.39",
    brutto="1136.12", brutto_quote="Brutto 1.136,12 €", positions=[],
)


def _stub(output_type, payloads):
    calls = []
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        payload = payloads[min(len(calls) - 1, len(payloads) - 1)]
        tool = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=payload)])
    return Agent(FunctionModel(respond), output_type=output_type, system_prompt=SYSTEM_PROMPT), calls


def _run(monkeypatch, verify_payload):
    main, _ = _stub(ExtractedInvoice, [_BASE])
    verify, vcalls = _stub(VerifyOutput, [verify_payload])
    monkeypatch.setattr(invoice_mod, "extract_pages", lambda _: [PAGE])
    monkeypatch.setattr(invoice_mod, "find_embedded_invoice_xml", lambda _: None)
    doc = invoice_mod.extract_invoice(Path("fake.pdf"), agent=main, with_retry=False, verify_agent=verify)
    return doc, len(vcalls)


def test_verify_turn_corrects_a_misread_date(monkeypatch) -> None:
    doc, n = _run(monkeypatch, {"items": [{"field": "invoice_date", "basis": "stated",
                                           "value": "2026-09-09", "quote": "Datum: 09.09.2026"}]})
    assert n == 1
    assert doc.invoice_date == date(2026, 9, 9)
    ev = doc.evidence["invoice_date"]
    assert ev["status"] == "verified" and "korrigiert" in ev["note"] and ev["page"] == 1
    assert doc.evidence["netto"]["status"] == "verified"                # untouched, was fine


def test_verify_turn_absent_keeps_value_unresolved(monkeypatch) -> None:
    doc, n = _run(monkeypatch, {"items": [{"field": "invoice_date", "basis": "absent", "value": None, "quote": None}]})
    assert n == 1
    assert doc.invoice_date == date(2024, 4, 23)                        # never nulled, never guessed
    assert doc.evidence["invoice_date"]["status"] == "unresolved"


def test_verify_turn_rejects_an_ungrounded_claim(monkeypatch) -> None:
    doc, _ = _run(monkeypatch, {"items": [{"field": "invoice_date", "basis": "stated",
                                           "value": "2026-09-10", "quote": "Datum: 10.09.2026"}]})   # not in PAGE
    assert doc.invoice_date == date(2024, 4, 23)
    assert doc.evidence["invoice_date"]["status"] == "unresolved"


def test_no_call_when_nothing_is_doubtful(monkeypatch) -> None:
    good = dict(_BASE, invoice_date="2026-09-09", invoice_date_quote="Datum: 09.09.2026")
    main, _ = _stub(ExtractedInvoice, [good])
    verify, vcalls = _stub(VerifyOutput, [{"items": []}])
    monkeypatch.setattr(invoice_mod, "extract_pages", lambda _: [PAGE])
    monkeypatch.setattr(invoice_mod, "find_embedded_invoice_xml", lambda _: None)
    invoice_mod.extract_invoice(Path("fake.pdf"), agent=main, with_retry=False, verify_agent=verify)
    assert vcalls == []
