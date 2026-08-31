"""vne-generation unit suite — every deterministic layer of the VNE pipeline, no API calls.

The LLM extraction itself is exercised by the live corpus tests; here we pin the
config loader, the document classifier, vendor matching, ratio derivation
(including the Nachlass renormalization), the amount check, the compute core,
and the xlsx round-trip through the corpus ground-truth reader.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_controller.config import ProjektConfig, load_project_config
from invoice_controller.extract.classify import DocClass, classify_pdf
from invoice_controller.extract.invoice import check_invoice_amounts
from invoice_controller.llm.extract_invoice import ExtractedInvoice
from invoice_controller.match.vendor import is_own_company, match_vendor
from invoice_controller.models import (
    AmountCheck,
    InvoiceDocument,
    InvoiceType,
    Kostenkategorie,
    VatStatus,
)
from invoice_controller.vne.compute import compute_vne
from invoice_controller.vne.ratios import VendorRatio, from_kostenaufstellung_ods
from invoice_controller.vne.xlsx import write_vne_tabelle
from tests.corpus.vne_tabelle import parse_vne_tabelle

# --- config -----------------------------------------------------------------------------

def test_missing_projekt_yaml_yields_default_config(tmp_path: Path) -> None:
    cfg = load_project_config(tmp_path / "projekt.yaml")
    assert cfg.client is None
    assert not cfg.has_window
    assert cfg.window_ok(date(2026, 1, 1)) is None


def test_projekt_yaml_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "projekt.yaml").write_text(
        "client:\n  name: Zepa GmbH\n  address: Valdorfer Straße 100, 32602 Vlotho\n"
        "bewilligungszeitraum_start: 2023-03-23\n"
        "bewilligungszeitraum_end: 2026-03-30\n"
        "bescheid:\n  foerderbetrag: 31833\n  kostendeckel_foerderanteil: 0.4\n"
        "  agvo_referenzkosten: 0\n"
    )
    cfg = load_project_config(tmp_path / "projekt.yaml")
    assert cfg.client and cfg.client.name == "Zepa GmbH"
    assert cfg.window_ok(date(2023, 3, 22)) is False
    assert cfg.window_ok(date(2023, 3, 23)) is True
    assert cfg.bescheid and cfg.bescheid.foerderbetrag == Decimal("31833")


# --- classification ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Rg_L&R_1.Ar_Kältemaschine_RG0015642_27.03.2025.pdf", DocClass.INVOICE),
        ("Gutschrift MAFAC_2341322_12.05.2003.pdf", DocClass.INVOICE),
        ("akf Rechnung Auslösung.pdf", DocClass.INVOICE),
        ("ANG1330412_FORUS F 38 E_Reiling20220817MH.pdf", DocClass.OFFER),
        ("- 3. Reinigungsanlage MAFA, Angebot-Soll.pdf", DocClass.OFFER),
        ("Kostenaufstellung, Meyring.pdf", DocClass.OTHER),
        ("EK4_129- VNE-Tabelle EEW-7156180- Zepa.pdf", DocClass.OTHER),
        ("Leasingsvetrag akf leasing.pdf", DocClass.OTHER),
        # Since 2026-08-28 payment proofs are their own class (BEG needs them);
        # vne-generation still ignores them — anything non-invoice/-offer lands in `ignored`.
        ("akf Zahlungsnachweis Auslösung.pdf", DocClass.ZAHLUNGSNACHWEIS),
        ("Erklärung Meyring zur Vorkasse.pdf", DocClass.OTHER),
        ("Fragenkatalog Modul 4.pdf", DocClass.OTHER),
    ],
)
def test_classify_by_filename(name: str, expected: DocClass) -> None:
    got = classify_pdf(Path(name), first_page_text="")
    assert got.doc_class is expected, f"{name}: {got.doc_class} ({got.reason})"


def test_classify_mute_filename_uses_first_page_text() -> None:
    inv = classify_pdf(Path("scan001.pdf"), first_page_text="RECHNUNG ER299254\nDatum ...")
    assert inv.doc_class is DocClass.INVOICE
    off = classify_pdf(Path("doc7.pdf"), first_page_text="Wir danken für Ihre Anfrage. Angebot Nr. 4711")
    assert off.doc_class is DocClass.OFFER


def test_classify_undecidable_defaults_to_invoice_for_review() -> None:
    got = classify_pdf(Path("scan001.pdf"), first_page_text="")
    assert got.doc_class is DocClass.INVOICE
    assert "undecided" in got.reason


# --- vendor matching --------------------------------------------------------------------

def _ratio(vendor: str, ik: str = "0.9", nk: str = "0.1") -> VendorRatio:
    return VendorRatio(
        vendor=vendor, header=f"SOLL: {vendor} Angebot 1 vom 01.01.2026",
        anteil_ik=Decimal(ik), anteil_nk=Decimal(nk),
        beantragt_ik=Decimal("90000"), beantragt_nk=Decimal("10000"),
        gesamt=Decimal("100000"), sonderpreis=None, source="test",
    )


def test_vendor_match_across_legal_forms() -> None:
    ratios = [_ratio("L&R"), _ratio("MAFAC")]
    got = match_vendor("L&R Kältetechnik GmbH & Co.KG", ratios)
    assert got is not None and got.vendor == "L&R"


def test_vendor_match_requires_token_overlap() -> None:
    assert match_vendor("Hofmann Kran-Vermietung", [_ratio("MAFAC"), _ratio("Eggersmann")]) is None


def test_own_company_detection() -> None:
    assert is_own_company("ENERGIEKONZEPT Krause GmbH")
    assert is_own_company("EnergieKonzept")
    assert not is_own_company("Eggersmann GmbH")


# --- ratios from the cost-estimation .ods (uses the cost-estimation writer as fixture source) ---------------------

def test_ratios_from_kostenaufstellung_ods_with_nachlass_renormalize(tmp_path: Path) -> None:
    from invoice_controller.extract.cross_sum import check_offer
    from invoice_controller.models import OfferDocument, OfferHeader, OfferTotals, Position
    from invoice_controller.template.ods import write_kostenaufstellung

    positions = [
        Position(pos="1", description="Anlage", line_total_net=Decimal("90000"),
                 kategorie=Kostenkategorie.INVESTITIONSKOSTEN),
        Position(pos="2", description="Montage", line_total_net=Decimal("8000"),
                 kategorie=Kostenkategorie.NEBENKOSTEN),
        Position(pos="3", description="Rabatt", line_total_net=Decimal("-2000"),
                 kategorie=Kostenkategorie.NACHLASS),
    ]
    totals = OfferTotals(nettosumme=Decimal("96000"))
    offer = OfferDocument(
        source_path=tmp_path / "x.pdf",
        header=OfferHeader(vendor_name="Musterbau GmbH", offer_number="42", offer_date=date(2026, 1, 5)),
        positions=positions, totals=totals, cross_sum=check_offer(positions, totals),
    )
    ods = tmp_path / "Kostenaufstellung.ods"
    write_kostenaufstellung([offer], ods)

    ratios = from_kostenaufstellung_ods(ods)
    assert len(ratios) == 1
    r = ratios[0]
    assert "Musterbau" in r.vendor
    # Renormalized over IK+NK (90000+8000), the Nachlass share must not distort the split.
    assert abs(r.anteil_ik - Decimal("90000") / Decimal("98000")) < Decimal("0.0001")
    assert abs(r.anteil_ik + r.anteil_nk - 1) < Decimal("0.0001")
    assert r.beantragt_ik == Decimal("90000")
    assert r.beantragt_nk == Decimal("8000")


# --- amount check -----------------------------------------------------------------------

def _extracted(**kwargs) -> ExtractedInvoice:
    base = dict(
        vendor_name="V", invoice_number="1", invoice_date=date(2026, 1, 1),
        netto=Decimal("100.00"), mwst_pct=Decimal("19"), mwst_amount=Decimal("19.00"),
        brutto=Decimal("119.00"),
    )
    base.update(kwargs)
    return ExtractedInvoice(**base)


def test_amount_check_passes_on_consistent_invoice() -> None:
    assert check_invoice_amounts(_extracted()).passed


def test_amount_check_fails_on_brutto_mismatch() -> None:
    chk = check_invoice_amounts(_extracted(brutto=Decimal("120.00")))
    assert not chk.passed and "Brutto" in (chk.message or "")


def test_amount_check_tax_free_expects_brutto_equals_netto() -> None:
    ok = check_invoice_amounts(_extracted(
        vat_status=VatStatus.TAX_FREE, mwst_pct=None, mwst_amount=None, brutto=Decimal("100.00")))
    assert ok.passed
    bad = check_invoice_amounts(_extracted(
        vat_status=VatStatus.TAX_FREE, mwst_pct=None, mwst_amount=None, brutto=Decimal("119.00")))
    assert not bad.passed


def test_amount_check_flags_positive_gutschrift() -> None:
    chk = check_invoice_amounts(_extracted(invoice_type=InvoiceType.GUTSCHRIFT))
    assert not chk.passed and "Gutschrift" in (chk.message or "")


# --- compute ----------------------------------------------------------------------------

def _invoice(vendor: str, netto: str, *, number: str = "1", d: date = date(2026, 2, 1), **kwargs) -> InvoiceDocument:
    return InvoiceDocument(
        source_path=Path(f"{vendor}_{number}.pdf"), vendor_name=vendor, invoice_number=number,
        invoice_date=d, netto=Decimal(netto),
        amount_check=AmountCheck(passed=True), **kwargs,
    )


def test_compute_splits_by_vendor_ratio() -> None:
    ratios = [_ratio("MAFAC", "0.9489", "0.0511")]
    result = compute_vne([_invoice("MAFAC GmbH", "10000.00")], ratios, ProjektConfig())
    row = result.invoices[0]
    assert row.ik_amount == Decimal("9489.00")
    assert row.nk_amount == Decimal("511.00")
    assert result.sum_ik + result.sum_nk == Decimal("10000.00")


def test_compute_own_company_goes_to_ek() -> None:
    result = compute_vne(
        [_invoice("ENERGIEKONZEPT Krause GmbH", "3750.00")], [_ratio("MAFAC")], ProjektConfig()
    )
    row = result.invoices[0]
    assert row.own_company and row.ek_amount == Decimal("3750.00")
    assert result.beantragt_ek == Decimal("3750.00")  # Σ own invoices (decided 2026-08-24)


def test_compute_unmatched_vendor_gets_flag_and_no_split() -> None:
    result = compute_vne([_invoice("Hofmann Kran", "500.00")], [_ratio("MAFAC")], ProjektConfig())
    row = result.invoices[0]
    assert not row.splits
    assert any("kein Angebot" in f for f in row.flags)
    assert result.sum_ik == 0


def test_compute_gutschrift_reduces_sums() -> None:
    ratios = [_ratio("MAFAC", "1", "0")]
    invoices = [
        _invoice("MAFAC", "10000.00"),
        _invoice("MAFAC", "-2000.00", number="G1", invoice_type=InvoiceType.GUTSCHRIFT),
    ]
    result = compute_vne(invoices, ratios, ProjektConfig())
    assert result.sum_ik == Decimal("8000.00")


def test_compute_window_check() -> None:
    cfg = ProjektConfig(
        bewilligungszeitraum_start=date(2025, 1, 1), bewilligungszeitraum_end=date(2026, 1, 1)
    )
    result = compute_vne([_invoice("MAFAC", "100.00", d=date(2026, 6, 1))], [_ratio("MAFAC")], cfg)
    row = result.invoices[0]
    assert row.window_ok is False
    assert any("Bewilligungszeitraum" in f for f in row.flags)


def test_compute_ansetzbar_is_min_of_beantragt_and_actual() -> None:
    ratios = [_ratio("MAFAC", "1", "0")]  # beantragt_ik = 90000
    result = compute_vne([_invoice("MAFAC", "95000.00")], ratios, ProjektConfig())
    assert result.ansetzbar("IK") == Decimal("90000")  # capped at beantragt


def test_compute_anzahlungskette_mismatch_is_flagged() -> None:
    ratios = [_ratio("LR", "1", "0")]
    invoices = [
        _invoice("LR", "50000.00", number="A1", invoice_type=InvoiceType.ANZAHLUNGSRECHNUNG),
        _invoice("LR", "10000.00", number="S1", invoice_type=InvoiceType.SCHLUSSRECHNUNG,
                 cumulative_netto=Decimal("62000.00"), deducted_advances_netto=Decimal("52000.00")),
    ]
    result = compute_vne(invoices, ratios, ProjektConfig())
    schluss = result.invoices[1]
    assert any("Anzahlungskette" in f for f in schluss.flags)


def test_compute_foerderbetrag_chain() -> None:
    cfg = ProjektConfig.model_validate({
        "bescheid": {"foerderbetrag": "30000", "kostendeckel_foerderanteil": "0.4",
                     "agvo_referenzkosten": "0"},
    })
    result = compute_vne([_invoice("MAFAC", "100000.00")], [_ratio("MAFAC", "1", "0")], cfg)
    assert result.mehrkosten == Decimal("100000.00")
    assert result.max_foerderbetrag == Decimal("40000.00")
    assert result.foerderbetrag_tatsaechlich == Decimal("30000")  # lower-of rule


# --- xlsx round-trip through the corpus ground-truth reader -----------------------------

def test_xlsx_roundtrip_parseable_by_corpus_reader(tmp_path: Path) -> None:
    ratios = [_ratio("MAFAC", "0.9489", "0.0511")]
    invoices = [
        _invoice("MAFAC GmbH", "10000.00", number="R1"),
        _invoice("ENERGIEKONZEPT Krause GmbH", "3750.00", number="RE-1"),
        _invoice("Hofmann Kran", "500.00", number="X9"),  # red-flag row
    ]
    result = compute_vne(invoices, ratios, ProjektConfig())
    out = tmp_path / "VNE-Tabelle.xlsx"
    write_vne_tabelle(result, ProjektConfig(), out)

    parsed = parse_vne_tabelle(out)
    assert len(parsed.invoices) == 3
    assert abs(parsed.sum_ik - result.sum_ik) < Decimal("0.01")
    assert abs(parsed.sum_nk - result.sum_nk) < Decimal("0.01")
    assert abs(parsed.sum_ek - result.sum_ek) < Decimal("0.01")
    # The red-flagged invoice contributes no split amounts but is present as a row.
    flagged = [i for i in parsed.invoices if "Hofmann" in i.recipient]
    assert flagged and flagged[0].ik_amount == 0


def test_classify_bafa_vne_forms_as_other() -> None:
    # BAFA's own Verwendungsnachweis forms live in project folders (eewvn_/qstvn_)
    # and previously leaked into the invoice table with the form's total as netto.
    for name in ("eewvn_19197530.pdf", "qstvn_18747772.pdf"):
        got = classify_pdf(Path(name), first_page_text="")
        assert got.doc_class is DocClass.OTHER, name


def test_anzahlungskette_includes_gutschrift() -> None:
    # ZePa/MAFAC: 33.400 + 41.750 − 5.332,77 (Gutschrift) = 69.817,23 deducted.
    ratios = [_ratio("MAFAC", "1", "0")]
    invoices = [
        _invoice("MAFAC", "33400.00", number="A1", invoice_type=InvoiceType.ANZAHLUNGSRECHNUNG),
        _invoice("MAFAC", "41750.00", number="A2", invoice_type=InvoiceType.TEILRECHNUNG),
        _invoice("MAFAC", "-5332.77", number="G1", invoice_type=InvoiceType.GUTSCHRIFT),
        _invoice("MAFAC", "13682.77", number="S1", invoice_type=InvoiceType.SCHLUSSRECHNUNG,
                 cumulative_netto=Decimal("83500.00"),
                 deducted_advances_netto=Decimal("69817.23")),
    ]
    result = compute_vne(invoices, ratios, ProjektConfig())
    schluss = result.invoices[3]
    assert not any("Anzahlungskette" in f for f in schluss.flags)


# --- polish round 2026-08-25: fixes validated against the full corpus sweep -------------

def test_duplicate_invoice_is_flagged_and_not_double_counted() -> None:
    # Dannemann: the same Mainka invoice ships twice under different filenames.
    ratios = [_ratio("Mainka", "0.9288", "0.0712")]
    invoices = [
        _invoice("Mainka Bau GmbH & Co. KG.", "253477.03", number="522390-1002"),
        _invoice("Mainka", "253477.03", number="522390-1002"),
    ]
    result = compute_vne(invoices, ratios, ProjektConfig())
    assert not result.invoices[0].unusable
    assert result.invoices[1].unusable
    assert any("Duplikat" in f for f in result.invoices[1].flags)
    assert result.sum_ik + result.sum_nk == Decimal("253477.03")  # counted once


def test_duplicate_own_invoice_does_not_inflate_beantragt_ek() -> None:
    invoices = [
        _invoice("EnergieKonzept", "2500.00", number="RE-23/376-G"),
        _invoice("ENERGIEKONZEPT", "2500.00", number="RE-23/376-G"),
    ]
    result = compute_vne(invoices, [], ProjektConfig())
    assert result.beantragt_ek == Decimal("2500.00")
    assert result.sum_ek == Decimal("2500.00")


def test_same_number_different_amount_is_not_a_duplicate() -> None:
    # e.g. an Abschlag and its Storno share a number but differ in amount.
    ratios = [_ratio("MAFAC", "1", "0")]
    invoices = [
        _invoice("MAFAC", "10000.00", number="42"),
        _invoice("MAFAC", "5000.00", number="42"),
    ]
    result = compute_vne(invoices, ratios, ProjektConfig())
    assert not any(r.unusable for r in result.invoices)


def test_zero_netto_extraction_is_marked_unusable() -> None:
    result = compute_vne([_invoice("Flelbilz Dies", "0.00")], [], ProjektConfig())
    row = result.invoices[0]
    assert row.unusable
    assert any("unbrauchbar" in f for f in row.flags)
    assert not row.splits


def test_vendor_match_concatenated_name() -> None:
    # Thess: "EAGLEVIZION" block vs "Eagle Vizion (6511660 Canada Inc.)" letterhead.
    ratios = [_ratio("EAGLEVIZION", "0.9566", "0.0434"), _ratio("SIKOPLAST")]
    got = match_vendor("Eagle Vizion ( 6511660 Canada Inc. )", ratios)
    assert got is not None and got.vendor == "EAGLEVIZION"


def test_vendor_match_glued_legal_form_and_umlaut_slip() -> None:
    # Jacob: extraction yields "RächerGmbH&Co.KG"; the offer block says "RÖCHER GmbH & Co. KG".
    ratios = [_ratio("Fa. RÖCHER GmbH & Co. KG"), _ratio("SIKOPLAST")]
    got = match_vendor("RächerGmbH&Co.KG", ratios)
    assert got is not None and "RÖCHER" in got.vendor


def test_vendor_from_header_after_date() -> None:
    from invoice_controller.vne.ratios import vendor_from_header

    got = vendor_from_header("SOLL Angebot.Nr: 20220824-0935-03 vom 24.04.2023 Fa. RÖCHER GmbH & Co. KG")
    assert "RÖCHER" in got


def test_choose_block_figures_prefers_consistent_sonderpreis_row() -> None:
    # KRR: ratio must come from the Sonderpreis row (195.400/205.000 = 0,95317),
    # not the Gesamtpreis row (222.800/232.400 = 0,9587).
    from invoice_controller.vne.ratios import choose_block_figures

    got = choose_block_figures(
        totals=[Decimal("232400.00"), Decimal("222800.00"), Decimal("9600.00"), Decimal("0.00")],
        sonder_totals=[Decimal("205000.00"), Decimal("195400.00"), Decimal("9600.00"), Decimal("27400.00")],
        pcts=[Decimal("100.00"), Decimal("95.32"), Decimal("4.68")],
    )
    assert got is not None
    gesamt, ik, nk, sonderpreis = got
    assert (gesamt, ik, nk) == (Decimal("205000.00"), Decimal("195400.00"), Decimal("9600.00"))
    assert sonderpreis == Decimal("205000.00")


def test_choose_block_figures_falls_back_to_pct_when_money_rows_disagree() -> None:
    # WHW: the Sonderpreis row's D/E columns are not scaled IK/NK (100/0 vs the
    # printed 93,25/6,75 % row) — the % row must win.
    from invoice_controller.vne.ratios import choose_block_figures

    got = choose_block_figures(
        totals=[Decimal("108850.00")],
        sonder_totals=[Decimal("83500.00"), Decimal("83500.00"), Decimal("0.00"), Decimal("7500.00")],
        pcts=[Decimal("100.00"), Decimal("93.25"), Decimal("6.75"), Decimal("6.89")],
    )
    assert got is not None
    gesamt, ik, nk, _ = got
    assert gesamt == Decimal("108850.00")
    assert abs(ik / (ik + nk) - Decimal("0.9325")) < Decimal("0.0001")


def test_choose_block_figures_keeps_cent_precise_sum_row() -> None:
    # ZePa: Σ row money values agree with the % row → keep them (full precision).
    from invoice_controller.vne.ratios import choose_block_figures

    got = choose_block_figures(
        totals=[Decimal("83599.00"), Decimal("79323.00"), Decimal("4276.00"), Decimal("0.00")],
        sonder_totals=[],
        pcts=[Decimal("100.00"), Decimal("94.89"), Decimal("5.11")],
    )
    assert got is not None
    _, ik, nk, _ = got
    assert ik == Decimal("79323.00") and nk == Decimal("4276.00")


def test_own_company_ratio_block_excluded_from_beantragt() -> None:
    # Presspart: the Kostenaufstellung PDF yields a spurious "EnergieKonzept" block
    # (a summary section) — it must not inflate beantragt NK.
    ratios = [
        _ratio("L&R", "0.8945", "0.1055"),
        VendorRatio(
            vendor="EnergieKonzept Krause GmbH", header="Honorar",
            anteil_ik=Decimal(0), anteil_nk=Decimal(1),
            beantragt_ik=Decimal(0), beantragt_nk=Decimal("323810"),
            gesamt=Decimal("323810"), sonderpreis=None, source="pdf",
        ),
    ]
    result = compute_vne([_invoice("L&R", "1000.00")], ratios, ProjektConfig())
    assert result.beantragt_nk == Decimal("10000")  # only the real vendor block


def _statement_ratio(header: str = "SOLL: SCHÄTZUNG lt. Stellungnahme Kunde vom 01.01.2026") -> VendorRatio:
    return VendorRatio(
        vendor=header, header=header,
        anteil_ik=Decimal(0), anteil_nk=Decimal(1),
        beantragt_ik=Decimal(0), beantragt_nk=Decimal("24017"),
        gesamt=Decimal("24017"), sonderpreis=None, source="pdf",
    )


def test_offerless_vendor_falls_back_to_single_statement_block() -> None:
    # NaturinForm: small trades without an offer are split by the Kostenschätzung
    # block's ratio (100 % NK), red-flagged "lt. Schätzung — prüfen" (2026-08-25).
    ratios = [_ratio("L&R", "0.9407", "0.0593"), _statement_ratio()]
    result = compute_vne([_invoice("Zirkelbach Kühltechnik", "5000.00")], ratios, ProjektConfig())
    row = result.invoices[0]
    assert row.nk_amount == Decimal("5000.00")
    assert row.ik_amount == 0
    assert any("lt. Schätzung" in f for f in row.flags)


def test_offerless_vendor_with_two_statement_blocks_stays_manual() -> None:
    ratios = [_statement_ratio(), _statement_ratio("Soll-Nebenostenschätzung: beigefügtes Schreiben")]
    result = compute_vne([_invoice("Zirkelbach", "5000.00")], ratios, ProjektConfig())
    row = result.invoices[0]
    assert not row.splits
    assert any("manuell kategorisieren" in f for f in row.flags)
