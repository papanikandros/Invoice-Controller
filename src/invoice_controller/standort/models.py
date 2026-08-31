"""Typed models for location-description — the client Standortbeschreibung (section 1.2 of the Antrag)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Verfahren(BaseModel):
    """A production process with a one-line German explanation (as in the examples)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    erklaerung: str = ""


class CompanyProfile(BaseModel):
    """Structured facts extracted (by the LLM) from the client's website.

    Only the website-derived data class lives here. Geo facts and the operational
    boilerplate are supplied separately and merged deterministically by the assembler.
    """

    model_config = ConfigDict(extra="forbid")

    branche: str = ""                 # "der Metall- und Kunststoffbearbeitung"
    produkte: list[str] = Field(default_factory=list)
    sektoren: list[str] = Field(default_factory=list)   # target industries/Bereiche
    leistungen: list[str] = Field(default_factory=list)  # services (Beratung, QM, Logistik …)
    verfahren: list[Verfahren] = Field(default_factory=list)
    groesse: str = ""                 # size hint: "kleines" / "mittleres" / "großes" / "Groß-"


# Working hours derived from the shift model (consultant rule, 2026-07-17):
# 1-Schicht 08:00–16:00, 2-Schicht 08:00–24:00 (i.e. to midnight), 3-Schicht 08:00–08:00 (24 h).
SHIFT_HOURS: dict[int, tuple[str, str]] = {
    1: ("08:00", "16:00"),
    2: ("08:00", "24:00"),
    3: ("08:00", "08:00"),
}


class OperationalDefaults(BaseModel):
    """Shift model → working hours. When sourced from the Fragenkatalog these are real
    data (`is_assumed=False`); with no source they fall back to a flagged 2-shift default."""

    model_config = ConfigDict(extra="forbid")

    schicht_anzahl: int = 2                # N-Schichtbetrieb
    betriebszeit_von: str = "08:00"
    betriebszeit_bis: str = "24:00"
    is_assumed: bool = True                # drives the "bitte prüfen" review flag

    @classmethod
    def from_shift(cls, schicht_anzahl: int, *, assumed: bool = False) -> OperationalDefaults:
        von, bis = SHIFT_HOURS.get(schicht_anzahl, SHIFT_HOURS[2])
        return cls(schicht_anzahl=schicht_anzahl, betriebszeit_von=von,
                   betriebszeit_bis=bis, is_assumed=assumed)


class StandortInput(BaseModel):
    """The per-client input to location-description."""

    model_config = ConfigDict(extra="forbid")

    firma: str                              # Antragsteller (legal name)
    strasse: str = ""
    plz: str = ""
    stadt: str = ""
    website: str | None = None
    # Untervermieter case: the described operating tenant differs from the Antragsteller.
    betreiberfirma: str | None = None
    # Größe from the Fragenkatalog (KU/KKU→kleines, MU→mittleres, GU→großes); overrides the
    # LLM's website-derived size hint when set.
    groesse: str = ""
    operational: OperationalDefaults = Field(default_factory=OperationalDefaults)


class Standortbeschreibung(BaseModel):
    """location-description result: the assembled German text plus the review flags it carries."""

    model_config = ConfigDict(extra="forbid")

    text: str
    review_notes: list[str] = Field(default_factory=list)
