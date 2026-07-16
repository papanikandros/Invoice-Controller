from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from invoice_controller.llm.extract import SYSTEM_PROMPT, ExtractedOffer
from invoice_controller.llm.summarize import SYSTEM_PROMPT as SUMMARIZE_PROMPT
from invoice_controller.models import CostNarrative

PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = PROJECT_ROOT / "examples"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(project: str, stem: str) -> dict[str, Any]:
    path = FIXTURES_DIR / project / f"{stem}.json"
    return json.loads(path.read_text())


def _collect_user_text(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                chunks.append(content)
    return "\n".join(chunks)


def make_fingerprint_agent(fingerprints: dict[str, dict[str, Any]]) -> Agent[None, ExtractedOffer]:
    """Agent stub that returns a pre-recorded LLM response based on which fingerprint string
    appears in the user prompt. Used by unit tests so they never hit a live API."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        user_text = _collect_user_text(messages)
        for fingerprint, payload in fingerprints.items():
            if fingerprint in user_text:
                tool_name = info.output_tools[0].name if info.output_tools else "final_result"
                return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=payload)])
        raise RuntimeError(
            f"stub agent has no fixture matching the prompt. Known fingerprints: {list(fingerprints)}"
        )

    return Agent(
        FunctionModel(respond),
        output_type=ExtractedOffer,
        system_prompt=SYSTEM_PROMPT,
    )


@pytest.fixture
def stub_agent_ek4_204() -> Agent[None, ExtractedOffer]:
    fingerprints = {
        "atb Elektronische Steuerungen": load_fixture("ek4_204", "atb"),
        "MUNK GmbH": load_fixture("ek4_204", "munk"),
        "L&R Kältetechnik": load_fixture("ek4_204", "lr"),
    }
    return make_fingerprint_agent(fingerprints)


@pytest.fixture
def stub_agent_ek4_322_statement() -> Agent[None, ExtractedOffer]:
    return make_fingerprint_agent(
        {"Werksverrohrung": load_fixture("ek4_322", "craemer_stellungnahme")}
    )


def make_summarize_agent(narrative: dict[str, Any]) -> Agent[None, CostNarrative]:
    """Stub summarizer that always returns the given CostNarrative payload, so narrative
    tests never hit a live API."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_name = info.output_tools[0].name if info.output_tools else "final_result"
        return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=narrative)])

    return Agent(
        FunctionModel(respond),
        output_type=CostNarrative,
        system_prompt=SUMMARIZE_PROMPT,
    )


@pytest.fixture
def stub_summarize_agent() -> Agent[None, CostNarrative]:
    return make_summarize_agent(
        {
            "investitionskosten_items": "das Roh- und Fertigmaterialhandling sowie den Zweischneckenextruder",
            "investitionskosten_seiten": [2, 3],
            "nebenkosten_items": "die Stahlbühne, das Engineering sowie die Inbetriebnahme",
            "nebenkosten_seiten": [3],
        }
    )


@pytest.fixture
def ek4_204_dir() -> Path:
    return EXAMPLES_DIR / "EK4_204"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run E2E tests that hit the live LLM API (costs money, requires API key).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live to enable")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
