"""F1 directory collection skips the Fragenkatalog and the tool's own outputs."""

from __future__ import annotations

import pytest

from invoice_controller.cli import _collect_pdfs, _is_offer_pdf


@pytest.mark.parametrize("name,is_offer", [
    ("Angebot_Reflex_Winkelmann_19.06.2026.pdf", True),
    ("4. L&R Angebot-SOLL.pdf", True),
    ("- 3. Reinigungsanlage MAFA, Angebot-Soll.pdf", True),
    ("7. Craemer Stellungnahme.pdf", True),  # statements are valid F1 inputs
    ("Fragenkatalog Modul 4.pdf", False),
    ("Kostenaufstellung, Winkelmann.pdf", False),
    ("VNE-Tabelle Investitionskosten.pdf", False),
    ("Standortbeschreibung.pdf", False),
    ("Rg MAFAC_ 1. AR_2341075 _04.05.2023.pdf", False),   # invoice
    ("Rg_L&R_1.Ar_Kältemaschine_RG0016691.pdf", False),   # invoice
    ("Gutschrift MAFAC_2341322_12.05.2003.pdf", False),   # credit note
    ("akf Rechnung Auslösung.pdf", False),
    ("eewvn_18747772.pdf", False),                         # BAFA VNE form
])
def test_is_offer_pdf(name, is_offer):
    assert _is_offer_pdf(name) is is_offer


def test_collect_pdfs_filters_non_offers(tmp_path):
    for n in ["Angebot_X.pdf", "Fragenkatalog Modul 4.pdf", "Kostenaufstellung.pdf"]:
        (tmp_path / n).write_bytes(b"%PDF-1.4\n")
    selected = _collect_pdfs(tmp_path)
    assert [p.name for p in selected] == ["Angebot_X.pdf"]


def test_collect_pdfs_single_file_is_honoured(tmp_path):
    # An explicitly-passed single file is respected even if it looks non-offer.
    f = tmp_path / "Fragenkatalog Modul 4.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    assert _collect_pdfs(f) == [f]


def test_collect_pdfs_errors_when_only_non_offers(tmp_path):
    (tmp_path / "Fragenkatalog Modul 4.pdf").write_bytes(b"%PDF-1.4\n")
    with pytest.raises(Exception, match="no offer PDFs"):
        _collect_pdfs(tmp_path)
