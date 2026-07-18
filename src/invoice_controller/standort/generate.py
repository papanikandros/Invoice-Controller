"""F3 orchestrator: client input (+ website) -> Standortbeschreibung.

Pipeline: scrape website -> LLM CompanyProfile -> offline+OSM geo lookup ->
deterministic template assembly. Scrape/LLM are skippable (profile passed in)
so the assembly is unit-testable without network or API.
"""

from __future__ import annotations

from ..geo import GeoInfo, lookup_geo
from .assemble import assemble_standort
from .models import CompanyProfile, Standortbeschreibung, StandortInput
from .profile import extract_company_profile
from .scrape import scrape_site_text


def generate_standort(
    inp: StandortInput,
    *,
    profile: CompanyProfile | None = None,
    geo: GeoInfo | None = None,
    online: bool = True,
) -> Standortbeschreibung:
    if profile is None:
        site_text = scrape_site_text(inp.website) if inp.website else ""
        profile = extract_company_profile(site_text)
    if geo is None:
        geo = lookup_geo(inp.plz, inp.stadt, online=online)
    result = assemble_standort(inp, profile, geo)
    if inp.website is None or not (profile.produkte or profile.verfahren or profile.branche):
        result.review_notes.insert(
            0, "Unternehmensprofil unvollständig (Webseite fehlt oder ohne Inhalt) — bitte ergänzen."
        )
    return result
