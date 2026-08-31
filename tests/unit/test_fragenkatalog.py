"""location-description Fragenkatalog parser — against the shipped sample form.

The only filled sample so far is examples/Fragenkatalog Modul 4.pdf (Winkelmann).
Real per-project Fragenkatalog PDFs are still to be delivered (PLAN.md FK-Q5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_controller.standort.fragenkatalog import (
    fragenkatalog_to_input,
    parse_fragenkatalog,
)
from invoice_controller.standort.models import SHIFT_HOURS, OperationalDefaults

SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "Winkelmann" / "Fragenkatalog Modul 4.pdf"

# The example corpus is client-confidential and not shipped in the repo; skip
# these tests gracefully on a fresh clone rather than failing to collect.
pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(), reason="confidential Fragenkatalog sample not present (see README)"
)


@pytest.fixture(scope="module")
def fk():
    return parse_fragenkatalog(SAMPLE)


def test_identity_and_address(fk):
    assert fk.firma == "Winkelmann Beteiligungs- und Verwaltungs GmbH"
    assert fk.strasse == "Schmalbachstr. 2"
    assert fk.plz == "59227"
    assert fk.stadt == "Ahlen"


def test_measure_location(fk):
    assert fk.massnahme_strasse == "Gersteinstr. 17"
    assert fk.massnahme_plz == "59227"
    assert fk.massnahme_stadt == "Ahlen"


def test_size_from_gu_checkbox(fk):
    assert fk.groesse == "großes"  # GU ticked


def test_shift_model(fk):
    assert fk.schicht_anzahl == 3  # "3-Schicht ausgenommen Wochenende"


def test_context_and_contact(fk):
    assert fk.betreiberfirma == "Betreiber ist die WBV"
    assert fk.wz_code == "6831"
    assert fk.email == "manfred.ernst@winkelmann-energy.de"
    assert "Druckluftzentrale" in fk.art_der_anlage


def test_to_input_prefers_measure_location_and_sets_hours(fk):
    inp = fragenkatalog_to_input(fk, website="https://winkelmann-energy.de")
    # geo uses the measure location; size + shift flow through as real (not assumed) data.
    assert (inp.plz, inp.stadt) == ("59227", "Ahlen")
    assert inp.strasse == "Gersteinstr. 17"
    assert inp.groesse == "großes"
    assert inp.operational.schicht_anzahl == 3
    assert inp.operational.is_assumed is False
    assert (inp.operational.betriebszeit_von, inp.operational.betriebszeit_bis) == SHIFT_HOURS[3]


def test_described_company_is_the_antragsteller_not_betriebsgesellschaft(fk):
    # The subject of the description must be the applicant, not the Betriebsgesellschaft.
    inp = fragenkatalog_to_input(fk)
    assert inp.firma == "Winkelmann Beteiligungs- und Verwaltungs GmbH"
    assert inp.betreiberfirma is None


@pytest.mark.parametrize("n,expected", [(1, ("08:00", "16:00")), (2, ("08:00", "24:00")),
                                        (3, ("08:00", "08:00"))])
def test_shift_hours_mapping(n, expected):
    op = OperationalDefaults.from_shift(n)
    assert (op.betriebszeit_von, op.betriebszeit_bis) == expected
    assert op.is_assumed is False
