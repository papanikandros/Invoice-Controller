from __future__ import annotations

from functools import lru_cache

from pydantic_ai import Agent

from invoice_controller.llm.extract import _resolve_model, _run_with_http_retry
from invoice_controller.models import CostNarrative

SYSTEM_PROMPT = """You write a short German prose summary of a vendor offer (Angebot) for an EEW Modul 4 energy-efficiency consulting workflow. The consultant pastes your summary into the Verwendungsnachweis (BAFA proof-of-funds-usage). Two summaries are produced per offer: one for the INVESTITIONSKOSTEN (capital goods) and one for the NEBENKOSTEN (ancillary services). Correctness over completeness: only describe what is actually in the document.

You receive the offer text with page markers like "<<PAGE 3>>". Pages are 1-based.

SOURCE: Base your summary on the offer's PRICE OVERVIEW / position list (the "Preisübersicht", "Angebotsübersicht", or the numbered Angebotspositionen with prices) — usually 1–3 pages near the front. Use the GROUP / SECTION HEADINGS and the main item names listed there. Do NOT mine the detailed technical-description pages later in the document, and do NOT cite them.

RULES:

(1) investitionskosten_items — a CONCISE German noun-phrase enumeration of the CAPITAL-GOODS scope (machines, plant, equipment, hardware, materials, components), built from the price-overview group headings / main item names. Example of the expected length and style: "das Roh- und Fertigmaterialhandling, die Dosierung & Zubehör, den Zweischneckenextruder, die Zusatzaggregate des Extruders, sowie die Schmelzepumpen, die Filtrationslösung und das Unterwassergranuliersystem". Use accusative case (the fragment follows "beinhalten": "den Zweischneckenextruder", not "der Zweischneckenextruder"). Do NOT add parenthetical sub-component lists, model codes, or technical specs. Do NOT start with "Die Investitionskosten" or include any euro amount or page reference — return ONLY the enumeration fragment.

(2) nebenkosten_items — same concise style and accusative case, for ANCILLARY SERVICES: Montage, Installation, Inbetriebnahme, Engineering, Planung, Projektierung, Schulung, Verpackung & Lieferung, Montageüberwachung, Wartung, Stahlbühne/Bühnenbau, Schnittstellen-Dienstleistungen (e.g. "die Stahlbühne, das Engineering, die Montageüberwachung, die Inbetriebnahme, die Verpackung und Lieferung CIP, die Schnittstelle zu OPM Farbmessgerät, sowie den Professional Check-Up"). If the offer has no ancillary-service items at all, return an empty string.

(3) OPTIONAL positions (lines marked Option/optionale Position/Eventualposition/Mehrpreis/Aufpreis, or prices in parentheses) belong in whichever enumeration fits their nature (goods -> investitionskosten_items, service -> nebenkosten_items), but you MUST mark them as optional in the prose, e.g. "sowie optional die FU-geregelten Verbraucherpumpen". Never silently omit a cost item.

(4) investitionskosten_seiten / nebenkosten_seiten — the 1-based page number(s) of the PRICE OVERVIEW where that category's items are listed (typically 1–3 pages, e.g. [2, 3]), plus the specific page of any optional item you folded in. Do NOT list the detailed technical-description pages. If a category has no items, return an empty list.

(5) Write in German, one clean sentence's worth of items per category — group headings and main item names, never full technical specifications.
"""


@lru_cache(maxsize=1)
def get_summarize_agent() -> Agent[None, CostNarrative]:
    return Agent(
        _resolve_model(),
        output_type=CostNarrative,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
    )


def summarize_offer_costs(
    pages: list[str],
    agent: Agent[None, CostNarrative] | None = None,
) -> CostNarrative:
    paginated = "\n".join(f"<<PAGE {i}>>\n{p}" for i, p in enumerate(pages, start=1))
    user = (
        "Below is the layout-preserved text of one German vendor offer PDF. "
        "Summarise its costs per the rules in the system prompt.\n\n"
        f"{paginated}"
    )
    runner = agent or get_summarize_agent()
    return _run_with_http_retry(runner, user)
