"""cost-estimation directory collection routes through the shared classifier
(2026-08-28) — only offer-classified PDFs enter the run; the Fragenkatalog, the
tool's own outputs, and invoices are skipped."""

from __future__ import annotations

import pytest

from invoice_controller.cli import _collect_offer_pdfs


def test_collect_pdfs_filters_non_offers(tmp_path):
    for n in ["Angebot_X.pdf", "Fragenkatalog Modul 4.pdf", "Kostenaufstellung.pdf"]:
        (tmp_path / n).write_bytes(b"%PDF-1.4\n")
    selected = _collect_offer_pdfs(tmp_path)
    assert [p.name for p in selected] == ["Angebot_X.pdf"]


def test_collect_pdfs_skips_invoices_and_bafa_forms(tmp_path):
    for n in [
        "4. L&R Angebot-SOLL.pdf",
        "7. Craemer Stellungnahme.pdf",   # statements are valid cost-estimation inputs
        "Rg MAFAC_ 1. AR_2341075 _04.05.2023.pdf",
        "Gutschrift MAFAC_2341322_12.05.2003.pdf",
        "eewvn_18747772.pdf",
        "VNE-Tabelle Investitionskosten.pdf",
    ]:
        (tmp_path / n).write_bytes(b"%PDF-1.4\n")
    selected = _collect_offer_pdfs(tmp_path)
    assert sorted(p.name for p in selected) == [
        "4. L&R Angebot-SOLL.pdf",
        "7. Craemer Stellungnahme.pdf",
    ]


def test_collect_pdfs_single_file_is_honoured(tmp_path):
    # An explicitly-passed single file is respected even if it looks non-offer.
    f = tmp_path / "Fragenkatalog Modul 4.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    assert _collect_offer_pdfs(f) == [f]


def test_collect_pdfs_errors_when_only_non_offers(tmp_path):
    (tmp_path / "Fragenkatalog Modul 4.pdf").write_bytes(b"%PDF-1.4\n")
    with pytest.raises(Exception, match="no offer PDFs"):
        _collect_offer_pdfs(tmp_path)
