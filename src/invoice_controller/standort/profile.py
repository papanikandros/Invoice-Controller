"""LLM extraction of a structured CompanyProfile from scraped website text (F3).

The LLM only fills the website-derived data class (Branche, Produkte, Sektoren,
Leistungen, Verfahren, Größe) — the German prose frame is assembled deterministically
in assemble.py. Reuses the provider selection + HTTP retry from llm/extract.py.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_ai import Agent

from ..llm.extract import _resolve_model, _run_with_http_retry
from .models import CompanyProfile

SYSTEM_PROMPT = """\
Du extrahierst aus dem Webseitentext eines Unternehmens strukturierte Fakten für die
Foerderantrags-Sektion "Beschreibung des Standortes". Antworte NUR mit den geforderten Feldern.

Regeln:
1. `branche`: eine kurze Genitiv-Phrase des Taetigkeitsbereichs, so dass sie hinter
   "aus dem Bereich " passt, z. B. "der Metall- und Kunststoffbearbeitung",
   "des Recyclings von Kunststoffen". Kein Satz, keine Firmennennung.
2. `produkte`: die konkreten Produkte/Erzeugnisse als kurze Substantiv-Fragmente.
3. `sektoren`: die belieferten Branchen/Einsatzbereiche (z. B. Maschinenbau, Medizintechnik).
4. `leistungen`: Dienstleistungen im Leistungsumfang (z. B. Beratung und Planung,
   Qualitaetsmanagement, Logistik). Ohne den Zusatz "weitere ergaenzende Dienstleistungen".
5. `verfahren`: eingesetzte Produktionsverfahren; je Verfahren ein `name` und eine
   knappe deutsche `erklaerung` (ein Satz), wie ein Unternehmen es einsetzt.
6. `groesse`: Groesseneinschaetzung als Adjektiv-Stamm — "kleines", "mittleres",
   "grosses" oder "Gross-" — nur wenn im Text erkennbar; sonst leer lassen.
7. Erfinde nichts. Was nicht im Text steht, bleibt leer.
8. Deutsche Sprache, sachlich, keine Werbesprache.
"""


@lru_cache(maxsize=1)
def get_profile_agent() -> Agent[None, CompanyProfile]:
    return Agent(_resolve_model(), output_type=CompanyProfile, system_prompt=SYSTEM_PROMPT)


def extract_company_profile(
    site_text: str, *, agent: Agent[None, CompanyProfile] | None = None
) -> CompanyProfile:
    if not site_text.strip():
        return CompanyProfile()
    runner = agent or get_profile_agent()
    prompt = f"Webseitentext des Unternehmens:\n\n{site_text}"
    return _run_with_http_retry(runner, prompt)
