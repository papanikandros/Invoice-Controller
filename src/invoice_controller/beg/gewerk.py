"""Gewerk label + section assignment per invoice (B5 row placement).

Deterministic first: the consultant's own company is always Baubegleitung, and
planner-profession keywords (Energieberater, Architekt, Statik, SiGeKo, Bauleitung)
route to Baubegleitung & Fachplanung. Everything else gets ONE small LLM call at
temperature 0 over the already-extracted fields (vendor, subject, top positions) —
works identically for text and vision extractions since it never re-reads the PDF.
"""

from __future__ import annotations

import re
from enum import Enum
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from invoice_controller.llm.extract import (
    DETERMINISTIC_SETTINGS,
    _resolve_model,
    _run_with_http_retry,
)
from invoice_controller.match.vendor import is_own_company
from invoice_controller.models import InvoiceDocument


class BegSection(str, Enum):
    MASSNAHME = "massnahme"
    BAUBEGLEITUNG = "baubegleitung"


class GewerkLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gewerk: str = Field(description="Kurzes deutsches Gewerk-Label, z.B. 'Zimmerei', 'WDVS', 'Elektroarbeiten'")
    section: BegSection


_PLANNER_RE = re.compile(
    r"energieberat|architekt|statik|sigeko|bauleit|fachplan|ingenieurbüro", re.IGNORECASE
)

SYSTEM_PROMPT = """Du ordnest Rechnungen eines geförderten Bauprojekts (BEG) einem Gewerk zu.

Regeln:
(G1) `gewerk`: ein kurzes deutsches Gewerk-Label wie auf einer Kostenzusammenstellung üblich — z.B. "Zimmerei", "Dachdeckung", "WDVS", "Maler", "Fenster & Haustür", "Elektroarbeiten", "Sanitär & Heizung", "Trockenbau", "Estrich und Dämmung", "Gerüstbau", "Blower-Door-Messung". Wähle das Label nach dem tatsächlichen Leistungsinhalt (Positionen), nicht nur nach dem Firmennamen.
(G2) `section`: "baubegleitung" NUR für Planungs-/Begleitleistungen (Energieberatung, Architekt, Bauleitung, Statik, SiGeKo, Fachplanung) — ausführende Bauleistungen sind IMMER "massnahme".
(G3) Antworte knapp und deterministisch; erfinde keine Details."""


@lru_cache(maxsize=1)
def get_gewerk_agent() -> Agent[None, GewerkLabel]:
    return Agent(
        _resolve_model(),
        output_type=GewerkLabel,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
        model_settings=DETERMINISTIC_SETTINGS,
    )


def label_invoice(
    invoice: InvoiceDocument, agent: Agent[None, GewerkLabel] | None = None
) -> GewerkLabel:
    if is_own_company(invoice.vendor_name):
        return GewerkLabel(gewerk="Energieberater", section=BegSection.BAUBEGLEITUNG)
    haystack = f"{invoice.vendor_name} {invoice.subject or ''}"
    if _PLANNER_RE.search(haystack):
        gewerk = "Architekt / Bauleiter" if re.search(
            r"architekt|bauleit", haystack, re.IGNORECASE
        ) else "Fachplanung"
        return GewerkLabel(gewerk=gewerk, section=BegSection.BAUBEGLEITUNG)

    top_positions = "; ".join(p.description for p in invoice.positions[:5])
    prompt = (
        f"Firma: {invoice.vendor_name}\n"
        f"Betreff: {invoice.subject or '—'}\n"
        f"Positionen (Auszug): {top_positions or '—'}"
    )
    runner = agent or get_gewerk_agent()
    return _run_with_http_retry(runner, prompt)
