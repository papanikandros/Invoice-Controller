"""A1 — client masking + local recipient check (PRIVACY.md §3).

The integration test proves the property that matters: with a configured client,
the LLM PAYLOAD contains the placeholders and not the client string, while the
deterministic recipient check still passes on the raw text.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from invoice_controller.extract import invoice as invoice_mod
from invoice_controller.llm.extract_invoice import SYSTEM_PROMPT, ExtractedInvoice
from invoice_controller.privacy import build_variants, mask_client, parse_empfaenger

CLIENT = "Wirox Oberflächentechnik GmbH & Co. KG"
ADDRESS = "Industriestraße 12, 32602 Vlotho"

PAGE = (
    "Muster Haustechnik GmbH · Rechnung 2026-114\n"
    f"{CLIENT}\nIndustriestraße 12\n32602 Vlotho\n"
    "Pos 1 Montage 1.000,00\nNettobetrag 1.000,00\n"
    f"Lieferung an WIROX Oberflächentechnik GmbH, Industriestr. 12"
)


class TestVariantsAndMasking:
    def test_variants_cover_legal_form_and_umlauts(self) -> None:
        names, addresses = build_variants("Müller Kältetechnik GmbH & Co. KG", "Hauptstraße 5, 44135 Dortmund")
        assert "Müller Kältetechnik GmbH & Co. KG" in names
        assert "Müller Kältetechnik" in names           # legal form stripped
        assert any("Mueller" in n for n in names)       # umlaut variant
        assert any("Hauptstr." in a for a in addresses)  # street abbreviation
        assert any(a.startswith("44135") for a in addresses)  # PLZ+Ort part alone

    def test_masking_replaces_all_forms_and_checks_recipient(self) -> None:
        report = mask_client([PAGE], CLIENT, ADDRESS)
        text = report.pages[0]
        assert "Wirox" not in text and "WIROX" not in text
        assert "Industriestraße 12" not in text and "Industriestr. 12" not in text
        assert "[KUNDE]" in text and "[KUNDENADRESSE]" in text
        assert report.recipient_found is True
        assert report.address_found is True
        # vendor and amounts untouched
        assert "Muster Haustechnik GmbH" in text
        assert "1.000,00" in text

    def test_wrong_client_is_detected_not_masked_away(self) -> None:
        report = mask_client([PAGE], "Ganz Andere Firma GmbH", None)
        assert report.recipient_found is False
        assert report.name_hits == 0

    def test_empfaenger_parse_from_bescheid_layout(self) -> None:
        text = (
            "Bundesamt für Wirtschaft und Ausfuhrkontrolle\n"
            "BAFA · Postfach 5171 · 65726 Eschborn\n"
            "Craemer GmbH\nBrocker Str. 1\n33442 Herzebrock-Clarholz\n"
            "Ihr Zeichen: … Datum: 18.06.2025\n"
        )
        parsed = parse_empfaenger(text)
        assert parsed == ("Craemer GmbH", "Brocker Str. 1, 33442 Herzebrock-Clarholz")

    def test_empfaenger_parse_returns_none_on_unknown_layout(self) -> None:
        assert parse_empfaenger("kein Adressblock hier") is None


class TestMaskedExtraction:
    PAYLOAD = {
        "vendor_name": "Muster Haustechnik GmbH",
        "recipient_name": "[KUNDE]",
        "invoice_number": "2026-114",
        "invoice_date": "2026-03-12",
        "netto": "1000.00",
        "positions": [{"pos": "1", "description": "Montage", "line_total_net": "1000.00"}],
    }

    def _capturing_agent(self, seen: list[str]) -> Agent:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            for m in messages:
                for part in m.parts:
                    if hasattr(part, "content") and isinstance(part.content, str):
                        seen.append(part.content)
            tool = info.output_tools[0].name if info.output_tools else "final_result"
            return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=self.PAYLOAD)])

        return Agent(FunctionModel(respond), output_type=ExtractedInvoice, system_prompt=SYSTEM_PROMPT)

    def test_llm_payload_is_masked_and_local_check_recorded(self, monkeypatch) -> None:
        seen: list[str] = []
        agent = self._capturing_agent(seen)
        monkeypatch.setattr(invoice_mod, "extract_pages", lambda _p: [PAGE])
        monkeypatch.setattr(invoice_mod, "find_embedded_invoice_xml", lambda _p: None)

        doc = invoice_mod.extract_invoice(
            Path("fake.pdf"), agent=agent, with_retry=False, mask=(CLIENT, ADDRESS)
        )
        payload = "\n".join(s for s in seen if "Montage" in s)
        assert "Wirox" not in payload and "[KUNDE]" in payload
        assert doc.masked is True
        assert doc.recipient_local_ok is True
        # placeholder residue from the model never lands in the document
        assert doc.recipient_name is None

    def test_without_mask_nothing_changes(self, monkeypatch) -> None:
        seen: list[str] = []
        agent = self._capturing_agent(seen)
        monkeypatch.setattr(invoice_mod, "extract_pages", lambda _p: [PAGE])
        monkeypatch.setattr(invoice_mod, "find_embedded_invoice_xml", lambda _p: None)
        doc = invoice_mod.extract_invoice(Path("fake.pdf"), agent=agent, with_retry=False)
        assert doc.masked is False and doc.recipient_local_ok is None
        assert any("Wirox" in s for s in seen)


def test_offer_masking(monkeypatch) -> None:
    from invoice_controller.extract import offer as offer_mod
    from invoice_controller.llm.extract import SYSTEM_PROMPT as OFFER_PROMPT, ExtractedOffer

    seen: list[str] = []
    payload = {
        "doc_type": "offer",
        "header": {"vendor_name": "Muster Haustechnik GmbH", "offer_number": "A-1",
                   "offer_date": "2026-01-10", "customer_name": "[KUNDE]"},
        "positions": [{"pos": "1", "description": "Montage", "line_total_net": "1000.00",
                       "kategorie": "nebenkosten"}],
        "totals": {"nettosumme": "1000.00"},
    }

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in m.parts:
                if hasattr(part, "content") and isinstance(part.content, str):
                    seen.append(part.content)
        tool = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=payload)])

    agent = Agent(FunctionModel(respond), output_type=ExtractedOffer, system_prompt=OFFER_PROMPT)
    monkeypatch.setattr(offer_mod, "extract_pages", lambda _p: [PAGE])
    doc = offer_mod.extract_offer(
        Path("fake.pdf"), agent=agent, with_narrative=False, with_retry=False,
        mask=(CLIENT, ADDRESS),
    )
    joined = "\n".join(s for s in seen if "Montage" in s)
    assert "Wirox" not in joined and "[KUNDE]" in joined
    assert doc.masked is True
    assert doc.header.customer_name is None
