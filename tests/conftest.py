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
    # Recorded fixtures embed real client/vendor offer data, so they are gitignored
    # and not shipped; fixture-backed tests skip automatically on a fresh clone.
    path = FIXTURES_DIR / project / f"{stem}.json"
    if not path.exists():
        pytest.skip("recorded response fixtures not present (see README)")
    return json.loads(path.read_text())


def load_cases(project: str) -> list[dict[str, Any]]:
    """Per-project case manifest (pdf filename + stub fingerprint per recorded offer).

    Lives beside the gitignored fixtures so no real vendor/offer identifiers appear in
    committed test source; tests read it and skip when the local corpus is absent."""
    path = FIXTURES_DIR / project / "_cases.json"
    if not path.exists():
        pytest.skip("recorded response fixtures not present (see README)")
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


def _agent_from_cases(project: str) -> Agent[None, ExtractedOffer]:
    fingerprints = {
        case["fingerprint"]: load_fixture(project, case["stem"]) for case in load_cases(project)
    }
    return make_fingerprint_agent(fingerprints)


@pytest.fixture
def stub_agent_ek4_204() -> Agent[None, ExtractedOffer]:
    return _agent_from_cases("ek4_204")


@pytest.fixture
def stub_agent_ek4_322_statement() -> Agent[None, ExtractedOffer]:
    return _agent_from_cases("ek4_322")


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
    # The example corpus is client-confidential and not shipped; tests that read
    # the real EK4_204 offer PDFs skip automatically on a fresh clone.
    d = EXAMPLES_DIR / "EK4_204"
    if not d.exists():
        pytest.skip("confidential EK4_204 corpus not present (see README)")
    return d


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
