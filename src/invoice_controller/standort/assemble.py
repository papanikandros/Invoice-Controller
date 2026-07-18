"""Deterministically assemble the German 'Beschreibung des Standortes' text.

Fixed 4-part template (see the examples/*/Beschreibung Standort.md): intro,
Produkte/Leistungen, Eingesetzte Verfahren, Standort. Variable slots come from
three data classes — website (CompanyProfile), geo (GeoInfo), and the flagged
operational boilerplate (OperationalDefaults). No LLM here; the prose frame is
house-style and stable so it can be regression-checked against the examples.
"""

from __future__ import annotations

from ..geo import GeoInfo
from .models import CompanyProfile, OperationalDefaults, Standortbeschreibung, StandortInput


def _join_de(items: list[str]) -> str:
    parts = [i.strip() for i in items if i and i.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " und " + parts[-1]


def _comma_join(items: list[str]) -> str:
    """Comma-only join (no terminal 'und') — used before a 'sowie …' closer."""
    return ", ".join(i.strip() for i in items if i and i.strip())


def _normalize_groesse(groesse: str) -> str:
    """Map the LLM's size hint to a neuter-nominative adjective ('… Unternehmen').

    Returns "" for unknown values (leaves the intro size-neutral, flagged separately).
    """
    g = groesse.strip().lower().rstrip("-")
    if not g:
        return ""
    if g.startswith("klein"):
        return "kleines"
    if g.startswith(("mittel", "mittler", "mittelständ", "mittelstand")):
        return "mittleres"
    if g.startswith(("groß", "gross")):
        return "großes"
    return ""


def _branche_phrase(branche: str) -> str:
    branche = branche.strip().rstrip(".")
    if not branche:
        return ""
    return f" aus dem Bereich {branche}"


def _region_phrase(geo: GeoInfo) -> str:
    if geo.kreis and geo.regierungsbezirk:
        return f" im {geo.kreis} im {geo.regierungsbezirk}, {geo.bundesland}"
    if geo.regierungsbezirk:
        return f" im {geo.regierungsbezirk}, {geo.bundesland}"
    if geo.kreis:
        return f" im {geo.kreis}, {geo.bundesland}" if geo.bundesland else f" im {geo.kreis}"
    if geo.bundesland:
        return f" in {geo.bundesland}"
    return ""


def _roads_phrase(geo: GeoInfo) -> str:
    parts = []
    if geo.bundesstrassen:
        parts.append("die Bundesstraße " + _join_de(geo.bundesstrassen))
    if geo.autobahnen:
        parts.append("die Bundesautobahn " + _join_de(geo.autobahnen))
    return " und ".join(parts)


def _intro(inp: StandortInput, profile: CompanyProfile, geo: GeoInfo) -> str:
    # Fragenkatalog size (KU/MU/GU) wins over the LLM's website-derived hint.
    groesse_word = _normalize_groesse(inp.groesse) or _normalize_groesse(profile.groesse)
    groesse = f"{groesse_word} " if groesse_word else ""
    land = f" (Bundesland {geo.bundesland})" if geo.bundesland else ""
    branche = _branche_phrase(profile.branche)
    subject = f"ein {groesse}Unternehmen in der Stadt {inp.stadt}{land}{branche}"
    if inp.betreiberfirma:
        return (
            f"Bei dem vom Antragssteller verkündigten Untervermieter, "
            f"{inp.betreiberfirma}, handelt es sich um {subject}."
        )
    return f"Bei der {inp.firma} handelt es sich um {subject}."


def _produkte(profile: CompanyProfile) -> str:
    sentences = []
    if profile.produkte:
        s = f"Das Unternehmen produziert {_join_de(profile.produkte)}"
        if profile.sektoren:
            s += f" für die Bereiche {_join_de(profile.sektoren)}"
        sentences.append(s + ".")
    if profile.leistungen:
        sentences.append(
            f"Deren Leistungsumfang besteht unter anderem aus {_comma_join(profile.leistungen)} "
            f"sowie weitere ergänzende Dienstleistungen."
        )
    return " ".join(sentences)


def _verfahren(profile: CompanyProfile) -> str:
    if not profile.verfahren:
        return ""
    lines = ["Eingesetzte Verfahren:"]
    for v in profile.verfahren:
        lines.append(f"{v.name}: {v.erklaerung}".rstrip(": ").rstrip() if not v.erklaerung
                     else f"{v.name}: {v.erklaerung}")
    return "\n".join(lines)


def _standort(inp: StandortInput, geo: GeoInfo, op: OperationalDefaults) -> tuple[str, str]:
    n = op.schicht_anzahl
    s1 = (
        f"Der Unternehmenssitz befindet sich in der Stadt {inp.stadt}{_region_phrase(geo)}. "
        f"Es liegt eine generelle Genehmigung für {n}-Schichtbetrieb vor, alle Medien wie "
        f"Strom, Gas und Wasser/Abwasser sind in ausreichender Dimensionierung vorhanden."
    )
    s2 = (
        f"Das Unternehmen produziert derzeit im klassischen {n}-Schicht-Betrieb. "
        f"Die Betriebszeiten bewegen sich im Wesentlichen in der Zeit von "
        f"{op.betriebszeit_von} bis {op.betriebszeit_bis} Uhr."
    )
    roads = _roads_phrase(geo)
    if roads:
        s3 = (
            f"Die Verkehrsanbindung ist als gut zu bezeichnen. In kurzer Fahrzeit sind {roads} "
            f"zu erreichen, wodurch ein zügiger Warentransport gewährleistet ist."
        )
    else:
        s3 = (
            "Die Verkehrsanbindung ist als gut zu bezeichnen. In kurzer Fahrzeit sind mehrere "
            "Bundesstraßen und Autobahnen zu erreichen, wodurch ein zügiger Warentransport "
            "gewährleistet ist."
        )
    return f"Standort:\n{s1}\n{s2}\n{s3}", roads


def assemble_standort(
    inp: StandortInput, profile: CompanyProfile, geo: GeoInfo
) -> Standortbeschreibung:
    op = inp.operational
    blocks = [_intro(inp, profile, geo), _produkte(profile), _verfahren(profile)]
    standort_block, roads = _standort(inp, geo, op)
    blocks.append(standort_block)
    text = "\n\n".join(b for b in blocks if b.strip())

    notes: list[str] = []
    if op.is_assumed:
        notes.append(
            f"Schichtbetrieb ({op.schicht_anzahl}-Schicht) und Betriebszeiten "
            f"({op.betriebszeit_von}–{op.betriebszeit_bis} Uhr) sind Standardannahmen — bitte prüfen."
        )
    if not roads:
        notes.append(
            "Verkehrsanbindung (konkrete Straßen) konnte nicht automatisch ermittelt werden "
            "— bitte ergänzen."
        )
    if not geo.online_enriched:
        notes.append("Kreis/Straßen wurden offline nicht vollständig ermittelt (kein OSM-Zugriff).")
    if not _normalize_groesse(inp.groesse) and not _normalize_groesse(profile.groesse):
        notes.append("Unternehmensgröße (klein/mittel/groß) nicht bestimmt — bitte einordnen.")

    return Standortbeschreibung(text=text, review_notes=notes)
