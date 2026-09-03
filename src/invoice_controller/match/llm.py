"""R6 — LLM matching augmentation, applied ONLY to what the deterministic scorer
left unmatched or medium-confidence.

Prompt design per Peeters & Bizer (MatchGPT, EDBT 2025 — adopted 2026-09-01):
serialize attribute VALUES without attribute names (names measurably hurt),
temperature 0, and ask for a structured explanation per pair — the Begründung and
Sicherheit land in the Abgleich sheet as the audit signal. Every LLM pair is a
PROPOSAL at medium confidence at best; the consultant confirms.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from invoice_controller.llm.extract import (
    DETERMINISTIC_SETTINGS,
    _resolve_model,
    _run_with_http_retry,
)
from invoice_controller.models import InvoicePosition, Position
from invoice_controller.normalize import format_de_decimal


class LlmPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rechnung_nr: int = Field(description="Index (R#) of the invoice position")
    angebot_nr: int | None = Field(
        default=None, description="Index (A#) of the matching offer position, null if none"
    )
    begruendung: str = Field(description="One short German sentence why (or why not)")
    sicherheit: float = Field(ge=0, le=1, description="Confidence 0..1")


class LlmMatches(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[LlmPair] = []


SYSTEM_PROMPT = """Du ordnest Rechnungspositionen den Angebotspositionen DESSELBEN Lieferanten zu (gefördertes Investitionsprojekt; die Zuordnung prüft ein Energieberater).

Regeln:
(M1) Jede Zeile ist als »Beschreibung — Betrag« gegeben. Gleiche Sache, anderer Wortlaut ist der Normalfall (Angebot: Typenbezeichnung; Rechnung: Liefertext). Synonyme, Abkürzungen und Teillieferungen erkennen.
(M2) Eine Rechnungsposition gehört zu HÖCHSTENS einer Angebotsposition. Mehrere Rechnungspositionen dürfen auf dieselbe Angebotsposition zeigen (Anzahlung/Teil-/Schlussrechnung).
(M3) Beträge sind ein Indiz, kein Beweis: Teilbeträge, Preisgleitung und Rabatte sind normal. Eine inhaltlich klare Zuordnung mit abweichendem Betrag ist eine gültige Zuordnung.
(M4) Wenn KEINE Angebotsposition passt, angebot_nr = null mit kurzer Begründung (z. B. Zusatzleistung, nicht angeboten).
(M5) Für jedes Paar eine knappe deutsche begruendung und eine ehrliche sicherheit (0..1). Nichts erfinden; im Zweifel null."""


@lru_cache(maxsize=1)
def get_match_agent() -> Agent[None, LlmMatches]:
    return Agent(
        _resolve_model(),
        output_type=LlmMatches,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
        model_settings=DETERMINISTIC_SETTINGS,
    )


def _line(description: str, amount) -> str:
    money = format_de_decimal(amount) + " €" if amount is not None else "ohne Betrag"
    return f"{description} — {money}"


def propose_matches_llm(
    offer_positions: list[Position],
    invoice_positions: list[InvoicePosition],
    invoice_indices: list[int],
    agent: Agent[None, LlmMatches] | None = None,
) -> LlmMatches:
    """`invoice_indices` selects which invoice positions still need help; indices in
    the result refer to the ORIGINAL lists (A# offer / R# invoice)."""
    offer_lines = "\n".join(
        f"A{j}: {_line(p.description, p.line_total_net)}" for j, p in enumerate(offer_positions)
    )
    invoice_lines = "\n".join(
        f"R{i}: {_line(invoice_positions[i].description, invoice_positions[i].line_total_net)}"
        for i in invoice_indices
    )
    prompt = (
        "Angebotspositionen:\n" + offer_lines +
        "\n\nZuzuordnende Rechnungspositionen:\n" + invoice_lines +
        "\n\nGib für jede R-Zeile genau ein pairs-Element zurück."
    )
    runner = agent or get_match_agent()
    return _run_with_http_retry(runner, prompt)
