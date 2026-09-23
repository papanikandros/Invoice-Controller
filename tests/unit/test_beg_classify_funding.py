"""B1 — the 6-class classifier over the BEG corpus naming conventions, the recursive
image-aware folder walk, and the FundingMeta extraction (stub agent, no API)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from invoice_controller.beg.funding import (
    SYSTEM_PROMPT,
    BegProgramType,
    ClientBasis,
    FundingMeta,
    extract_funding_meta,
)
from invoice_controller.extract.classify import (
    DocClass,
    classify_folder,
    classify_image,
    classify_pdf,
)

# --- BEG filename conventions (from examples/BEG/, names as shipped) --------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # invoices — EH/EM naming: AR/AZ prefixes, "Rech.", geprüft scans, Schluss/Abschlag
        ("1. AR Fissenewert 25.02.2025 BV Fissenewert.pdf", DocClass.INVOICE),
        ("1. AZ Mestekempter 24.09.2024 BV Fissenewert .pdf", DocClass.INVOICE),
        ("Rech. Machajewski 05.06.2025 BV Fissenewert.pdf", DocClass.INVOICE),
        ("Rechnung_Wolff_Klinkerbau_geprüft.pdf", DocClass.INVOICE),
        ("Schlussrechnung-RE-26_1813-Madroch-27-01-2026.pdf", DocClass.INVOICE),
        ("1. Abschlagsrechnung-RE-25_1457-Aepker-22-08-2025.pdf", DocClass.INVOICE),
        ("2101 Frima DWB 2. Abschlagsrechnung.pdf", DocClass.INVOICE),
        ("RE-23_695Storno.pdf", DocClass.INVOICE),
        ("Janus_RE-1197_geprüft.pdf", DocClass.INVOICE),
        # offers
        ("Angebot-ANG-25_1425-Madroch-17-06-2025.pdf", DocClass.OFFER),
        ("AN-1548_Dach.pdf", DocClass.OFFER),
        # funding documents
        ("antragsbestaetigung_HEIZUNGSFOERD-1.pdf", DocClass.ANTRAGSBESTAETIGUNG),
        ("Förderantrag_begem2_93456022.pdf", DocClass.ANTRAGSBESTAETIGUNG),
        ("tpb2_14565328.pdf", DocClass.ANTRAGSBESTAETIGUNG),
        ("begpt_17720963.pdf", DocClass.ANTRAGSBESTAETIGUNG),
        ("Zuwendungsbescheid.pdf", DocClass.ZUWENDUNGSBESCHEID),
        ("Zusageschreiben_KfW-Zuschuss_458000000632527.pdf", DocClass.ZUWENDUNGSBESCHEID),
        ("Festsetzungsbescheid .pdf", DocClass.ZUWENDUNGSBESCHEID),
        ("Auszahlungsbescheid.pdf", DocClass.ZUWENDUNGSBESCHEID),
        # payment proofs
        ("Zahlungsnachweis Madroch.pdf", DocClass.ZAHLUNGSNACHWEIS),
        ("Zahlungsnachweise.pdf", DocClass.ZAHLUNGSNACHWEIS),
        # other paperwork — Nachweis-side forms, Erklärungen, orders, tool outputs
        ("Verwendungsnachweis.pdf", DocClass.OTHER),
        ("begptvn_94301228.pdf", DocClass.OTHER),
        ("tpn2_18565035.pdf", DocClass.OTHER),
        ("Fachunternehmererklärung_Heizung.pdf", DocClass.OTHER),
        ("VdZ-Formular.pdf", DocClass.OTHER),
        ("JAZ.pdf", DocClass.OTHER),
        ("BEG_EM_Vollmacht_unterschrieben.pdf", DocClass.OTHER),
        ("Beauftragung_Hydraulischer Abgleich.pdf", DocClass.OTHER),
        ("AUF_lt._Angebot-ANG-26_1865-Schneider-23-01-2026.pdf", DocClass.OTHER),
        ("Auftrag_lt.ANG-25_1425-Madroch-17-06-2025.pdf", DocClass.OTHER),
        ("Bestätigung AN-1548.pdf", DocClass.OTHER),
        ("Heizlast_Krone&Deppe.pdf", DocClass.OTHER),
        ("Kostenzusammenstellung_EM_Aepker.pdf", DocClass.OTHER),
        ("Bestätigung nach Durchführung_Programm_6-1.pdf", DocClass.OTHER),
    ],
)
def test_classify_beg_filenames(name: str, expected: DocClass) -> None:
    got = classify_pdf(Path(name), first_page_text="")
    assert got.doc_class is expected, f"{name}: {got.doc_class} ({got.reason})"


def test_invoice_citing_its_auftragsbestaetigung_is_an_invoice() -> None:
    """EK4_333 (2026-09-23): all five L&R invoices were dropped as 'other' because
    the OTHER vocabulary (auftragsbestätigung) was checked before the invoice one."""
    text = (
        "L & R Kältetechnik GmbH & Co.KG, Hachener Straße 90a, 59846 Sundern\n"
        "MKT Mannel Kunststofftechnik\nANZAHLUNGSRECHNUNG\nMühlhofe 4b Belegnummer: RG0018118\n"
        "Wir berechnen Ihnen laut unserer Auftragsbestätigung - Nr.: 26-4132 vom 27.03.2026."
    )
    got = classify_pdf(Path("L&R-Kältetechnik-1. Ar-RG0018118-27.03.2026.pdf"), first_page_text=text)
    assert got.doc_class is DocClass.INVOICE
    # A document that is ONLY an order confirmation still lands in `other`.
    only = classify_pdf(Path("scan.pdf"), first_page_text="Auftragsbestätigung Nr. 4711\nLieferung KW 32")
    assert only.doc_class is DocClass.OTHER


def test_classify_image_payment_proof_by_name_or_folder() -> None:
    got = classify_image(Path("Zahlungsnachweise/3eb9419c.jpeg"))
    assert got.doc_class is DocClass.ZAHLUNGSNACHWEIS
    got = classify_image(Path("Zahlungsnachweis Dirker.png"))
    assert got.doc_class is DocClass.ZAHLUNGSNACHWEIS
    # A photo without payment context is `other`, reported not dropped.
    got = classify_image(Path("Innenarbeiten Maler/Stundenzettel Dämmung.jpeg"))
    assert got.doc_class is DocClass.OTHER


def test_classify_folder_recursive_with_images(tmp_path: Path) -> None:
    (tmp_path / "Rechnungen").mkdir()
    (tmp_path / "Zahlungsnachweise").mkdir()
    (tmp_path / "Rechnungen" / "Rechnung_X_geprüft.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "Zahlungsnachweise" / "beleg1.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "Zuwendungsbescheid.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / ".~lock.Kostenzusammenstellung.xlsx#").write_text("lock")

    got = {c.path.name: c.doc_class for c in classify_folder(tmp_path)}
    assert got == {
        "Rechnung_X_geprüft.pdf": DocClass.INVOICE,
        "beleg1.png": DocClass.ZAHLUNGSNACHWEIS,
        "Zuwendungsbescheid.pdf": DocClass.ZUWENDUNGSBESCHEID,
    }


def test_classify_folder_non_recursive_still_supported(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Rechnung_A.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "Rechnung_B.pdf").write_bytes(b"%PDF-1.4\n")
    got = classify_folder(tmp_path, recursive=False)
    assert [c.path.name for c in got] == ["Rechnung_B.pdf"]


def test_mute_pdf_in_invoice_folder_classified_by_parent(tmp_path: Path) -> None:
    # EM_Buttergasse: "img20260115_10080417.pdf" in Rechnungen/ — mute name, scan.
    d = tmp_path / "Rechnungen"
    d.mkdir()
    p = d / "img20260115_10080417.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    got = classify_pdf(p, first_page_text="")
    assert got.doc_class is DocClass.INVOICE


# --- FundingMeta extraction (stub agent) ------------------------------------------------

def _funding_stub(payload: dict) -> Agent[None, FundingMeta]:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=payload)])

    return Agent(FunctionModel(respond), output_type=FundingMeta, system_prompt=SYSTEM_PROMPT)


def test_extract_funding_meta_via_stub(tmp_path: Path) -> None:
    doc = tmp_path / "antragsbestaetigung_HEIZUNGSFOERD-1.pdf"
    doc.write_bytes(b"%PDF-1.4\n")
    agent = _funding_stub(
        {
            "program_type": "em",
            "program_label": "Heizungsförderung (KfW 458)",
            "vorgangsnummer": "428-2362-2256-6082",
            "antrag_date": "2025-09-18",
            "antragsteller_name": "Max Aepker",
            "client_basis": "privat",
            "client_basis_reason": "natürliche Person",
            "geplante_kosten_massnahmen": "33195",
            "foerdersatz_pct": "55",
            "foerderfaehige_kosten_cap": "30000",
            "baubegleitung_foerdersatz_pct": "50",
            "baubegleitung_kosten_cap": "5000",
        }
    )
    meta = extract_funding_meta([doc], agent=agent)
    assert meta.program_type is BegProgramType.EM
    assert meta.vorgangsnummer == "428-2362-2256-6082"
    assert meta.antrag_date == date(2025, 9, 18)
    assert meta.client_basis is ClientBasis.PRIVAT
    assert meta.geplante_kosten_massnahmen == Decimal("33195")
    assert meta.missing_fields() == []


def test_extract_funding_meta_empty_input_is_all_missing() -> None:
    meta = extract_funding_meta([])
    assert meta.program_type is BegProgramType.UNCLEAR
    missing = meta.missing_fields()
    assert "Vorgangsnummer" in missing
    assert "Brutto/Netto-Basis (privat/Unternehmen)" in missing
