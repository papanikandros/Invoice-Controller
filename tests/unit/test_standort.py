"""F3 Standortbeschreibung — assembly, input parsing, geo (offline), .docx writer.

No network/LLM: the scrape + profile extraction are stubbed via injected
CompanyProfile/GeoInfo, so these are fast and deterministic. The 6 shipped
`Beschreibung Standort.md` files are the fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_controller.geo import GeoInfo, offline_geo
from invoice_controller.standort.assemble import assemble_standort
from invoice_controller.standort.docx import write_standort_docx
from invoice_controller.standort.generate import generate_standort
from invoice_controller.standort.input_md import parse_expected_text, parse_header
from invoice_controller.standort.models import (
    CompanyProfile,
    OperationalDefaults,
    StandortInput,
    Verfahren,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
MD_PROJECTS = ["Dannemann", "Jacob", "KRR", "Meyring", "WHW", "ZePa"]

# The `Beschreibung Standort.md` fixtures are client-confidential and not shipped;
# skip the F3 suite gracefully on a fresh clone rather than failing to read them.
pytestmark = pytest.mark.skipif(
    not (EXAMPLES / "ZePa" / "Beschreibung Standort.md").exists(),
    reason="confidential Standort fixtures not present (see README)",
)

# Expected geo facts per PLZ (offline via pgeocode; Kreis is OSM-only, tested live).
EXPECTED_GEO = {
    "32602": ("Nordrhein-Westfalen", "Regierungsbezirk Detmold"),   # Vlotho
    "32457": ("Nordrhein-Westfalen", "Regierungsbezirk Detmold"),   # Porta Westfalica
    "32312": ("Nordrhein-Westfalen", "Regierungsbezirk Detmold"),   # Lübbecke
    "47533": ("Nordrhein-Westfalen", "Regierungsbezirk Düsseldorf"),  # Kleve
    "58739": ("Nordrhein-Westfalen", "Regierungsbezirk Arnsberg"),  # Wickede
}


def _profile() -> CompanyProfile:
    return CompanyProfile(
        branche="der Metallbearbeitung",
        produkte=["Präzisionsteile"],
        sektoren=["Maschinenbau", "Medizintechnik"],
        leistungen=["Beratung und Planung", "Qualitätsmanagement", "Logistik"],
        verfahren=[Verfahren(name="CNC-Fräsverfahren", erklaerung="Zur Herstellung der Teile.")],
        groesse="kleines",
    )


def _geo() -> GeoInfo:
    g = offline_geo("32602", "Vlotho")
    g.kreis = "Kreis Herford"
    g.autobahnen = ["A 2"]
    g.bundesstrassen = ["B 514", "B 611"]
    g.online_enriched = True
    return g


# ---- input parsing --------------------------------------------------------

@pytest.mark.parametrize("pid", MD_PROJECTS)
def test_parse_header(pid):
    inp = parse_header(EXAMPLES / pid / "Beschreibung Standort.md")
    assert inp.firma, f"{pid}: no company parsed"
    assert inp.plz.isdigit() and len(inp.plz) == 5, f"{pid}: bad PLZ {inp.plz!r}"
    assert inp.stadt, f"{pid}: no city parsed"


def test_parse_header_values_zepa():
    inp = parse_header(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    assert inp.firma == "Zepa GmbH"
    assert inp.plz == "32602"
    assert inp.stadt == "Vlotho"
    assert inp.strasse == "Valdorfer Straße 100"


def test_expected_text_recoverable():
    txt = parse_expected_text(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    assert txt.startswith("Bei der Zepa GmbH")


# ---- offline geo ----------------------------------------------------------

@pytest.mark.parametrize("plz,expected", EXPECTED_GEO.items())
def test_offline_geo(plz, expected):
    g = offline_geo(plz, "")
    assert (g.bundesland, g.regierungsbezirk) == expected


# ---- assembly (house style) ----------------------------------------------

def test_assembly_structure_and_boilerplate():
    inp = parse_header(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    res = assemble_standort(inp, _profile(), _geo())
    t = res.text
    assert t.startswith("Bei der Zepa GmbH handelt es sich um ein kleines Unternehmen "
                        "in der Stadt Vlotho (Bundesland Nordrhein-Westfalen) aus dem Bereich "
                        "der Metallbearbeitung.")
    assert "Eingesetzte Verfahren:\nCNC-Fräsverfahren: Zur Herstellung der Teile." in t
    assert "Standort:" in t
    assert ("alle Medien wie Strom, Gas und Wasser/Abwasser sind in ausreichender "
            "Dimensionierung vorhanden.") in t
    assert "im Kreis Herford im Regierungsbezirk Detmold, Nordrhein-Westfalen" in t
    assert "die Bundesstraße B 514 und B 611 und die Bundesautobahn A 2" in t
    # leistungen comma-joined before the 'sowie' closer (no terminal 'und Logistik').
    assert "Qualitätsmanagement, Logistik sowie weitere ergänzende Dienstleistungen" in t


@pytest.mark.parametrize("hint,expected", [
    ("mittel", "ein mittleres Unternehmen"),
    ("mittelständisch", "ein mittleres Unternehmen"),
    ("klein", "ein kleines Unternehmen"),
    ("großes", "ein großes Unternehmen"),
    ("Groß-", "ein großes Unternehmen"),
    ("", "ein Unternehmen"),
    ("xyz", "ein Unternehmen"),  # unknown -> size-neutral + flagged
])
def test_groesse_normalized_to_declined_adjective(hint, expected):
    inp = StandortInput(firma="X GmbH", plz="32602", stadt="Vlotho")
    prof = _profile()
    prof.groesse = hint
    res = assemble_standort(inp, prof, offline_geo("32602", "Vlotho"))
    assert res.text.startswith(f"Bei der X GmbH handelt es sich um {expected} ")
    if expected == "ein Unternehmen":
        assert any("Unternehmensgröße" in n for n in res.review_notes)


def test_untervermieter_variant():
    inp = StandortInput(firma="Meyring Vermögensverwaltungs UG & Co. KG", plz="32312",
                        stadt="Lübbecke", betreiberfirma="Meyring Kunststofftechnik GmbH & Co. KG")
    res = assemble_standort(inp, _profile(), offline_geo("32312", "Lübbecke"))
    assert res.text.startswith(
        "Bei dem vom Antragssteller verkündigten Untervermieter, "
        "Meyring Kunststofftechnik GmbH & Co. KG, handelt es sich um"
    )


def test_operational_default_is_flagged():
    inp = parse_header(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    res = assemble_standort(inp, _profile(), _geo())
    assert any("Standardannahmen" in n for n in res.review_notes)


def test_shift_count_flows_into_text():
    inp = parse_header(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    inp.operational = OperationalDefaults(schicht_anzahl=3, betriebszeit_von="06:00",
                                          betriebszeit_bis="06:00")
    res = assemble_standort(inp, _profile(), _geo())
    assert "3-Schichtbetrieb" in res.text and "3-Schicht-Betrieb" in res.text
    assert "von 06:00 bis 06:00 Uhr" in res.text


def test_missing_roads_flagged_and_neutral_sentence():
    inp = parse_header(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    g = offline_geo("32602", "Vlotho")  # no roads, not online_enriched
    res = assemble_standort(inp, _profile(), g)
    assert "mehrere Bundesstraßen und Autobahnen zu erreichen" in res.text
    assert any("Verkehrsanbindung" in n for n in res.review_notes)


# ---- generate() without network/LLM --------------------------------------

def test_generate_with_injected_profile_and_geo():
    inp = parse_header(EXAMPLES / "Jacob" / "Beschreibung Standort.md")
    res = generate_standort(inp, profile=_profile(), geo=_geo(), online=False)
    assert "Fr. Jacob Söhne GmbH & Co. KG" in res.text
    assert res.review_notes


# ---- .docx writer ---------------------------------------------------------

def test_write_docx(tmp_path):
    from docx import Document

    inp = parse_header(EXAMPLES / "ZePa" / "Beschreibung Standort.md")
    res = assemble_standort(inp, _profile(), _geo())
    out = write_standort_docx(res, inp, tmp_path / "zepa.docx")
    assert out.exists()
    doc = Document(str(out))
    body_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Zepa GmbH" in body_text
    assert "aus dem Bereich der Metallbearbeitung" in body_text
