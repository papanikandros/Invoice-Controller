"""E2 — cite-then-check for the fields E1 could not ground (2026-10-06).

One NARROW second LLM call per invoice, and only when at least one evidence status is
doubtful on a readable document (text or Tesseract path). The model is asked for just
those fields and must return, per field, the value and the verbatim passage that states
it, or `absent`. Code decides (zz-scraper `verify.py` pattern):

  stated + quote located + value inside the quote → value adopted (corrected if it
                                                    differed), status `verified`
  anything else                                   → status `unresolved`; the original
                                                    value is KEPT (never a guess, and
                                                    required fields cannot be nulled) —
                                                    it stays UNKLAR / yellow / flagged.

Cost: cents, and only for the ~1 in 10 invoices with a doubtful field.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from invoice_controller.extract.evidence import (
    DOUBTFUL,
    UNRESOLVED,
    VERIFIED,
    Evidence,
    locate,
    value_in_quote,
)
from invoice_controller.llm.extract import (
    DETERMINISTIC_SETTINGS,
    _resolve_model,
    _run_with_http_retry,
)

_DATE_FIELDS = {"invoice_date", "order_date"}
_DECIMAL_FIELDS = {"netto", "brutto"}


class VerifiedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    basis: Literal["stated", "absent"]
    value: str | None = Field(default=None, description="ISO date / decimal with dot / verbatim string, null when absent")
    quote: str | None = Field(default=None, description="verbatim passage (≤ 80 chars) stating the value, null when absent")


class VerifyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[VerifiedField]


SYSTEM_PROMPT = """You re-check specific fields of ONE German invoice whose first extraction could not be
confirmed against the document text. For EACH requested field, look again in the text and answer:
- basis "stated": the text states the value → return the value (dates ISO YYYY-MM-DD, amounts as
  decimal with a dot, strings verbatim) AND the shortest VERBATIM passage (≤ 80 characters, copied
  exactly, with its label) that states it. Never paraphrase or assemble a quote from several places.
- basis "absent": the text does not state it → value null, quote null.
Answer every requested field exactly once. An honest "absent" beats any guess."""


@lru_cache(maxsize=1)
def get_verify_agent() -> Agent[None, VerifyOutput]:
    return Agent(_resolve_model(), output_type=VerifyOutput, system_prompt=SYSTEM_PROMPT,
                 retries=2, model_settings=DETERMINISTIC_SETTINGS)


def _parse(field: str, raw: str | None):
    if raw is None or not str(raw).strip():
        return None
    raw = str(raw).strip()
    if field in _DATE_FIELDS:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    if field in _DECIMAL_FIELDS:
        try:
            return Decimal(raw.replace("€", "").replace(" ", "").replace(",", "."))
        except InvalidOperation:
            return None
    return raw


def verify_doubtful_fields(
    extracted,
    evidence: dict[str, Evidence],
    pages: list[str],
    *,
    agent: Agent[None, VerifyOutput] | None = None,
) -> dict[str, Evidence]:
    """Mutates `extracted` for fields the verify turn could confirm; returns the updated
    evidence mapping. No call when nothing is doubtful."""
    doubtful = [f for f, ev in evidence.items() if ev.status in DOUBTFUL]
    if not doubtful:
        return evidence
    current = {f: getattr(extracted, f, None) for f in doubtful}
    ask = "\n".join(f"- {f} (first extraction read: {current[f]!r})" for f in doubtful)
    text = "\n".join(f"<<PAGE {i}>>\n{p}" for i, p in enumerate(pages, start=1))
    message = (
        f"Fields to re-check:\n{ask}\n\nDocument text:\n{text[:40000]}"
    )
    runner = agent or get_verify_agent()
    try:
        out = _run_with_http_retry(runner, message)
    except Exception as exc:  # noqa: BLE001 — the verify turn is a second opinion, never a blocker
        for f in doubtful:
            evidence[f] = Evidence(status=UNRESOLVED, quote=evidence[f].quote,
                                   note=f"Prüfschritt fehlgeschlagen ({type(exc).__name__})")
        return evidence

    answered = {item.field: item for item in out.items if item.field in doubtful}
    for f in doubtful:
        item = answered.get(f)
        prior = evidence[f]
        if item is None or item.basis == "absent" or not item.quote:
            evidence[f] = Evidence(status=UNRESOLVED, quote=prior.quote, note="Prüfschritt: nicht bestätigt")
            continue
        value = _parse(f, item.value)
        page = locate(item.quote, pages)
        if value is None or page is None or not value_in_quote(value, item.quote):
            evidence[f] = Evidence(status=UNRESOLVED, quote=item.quote, page=page, note="Prüfschritt: Zitat/Wert nicht belegt")
            continue
        note = "Prüfschritt: bestätigt"
        if current[f] is not None and value != current[f]:
            note = f"Prüfschritt: korrigiert (vorher {current[f]})"
            setattr(extracted, f, value)
        elif current[f] is None:
            setattr(extracted, f, value)
        evidence[f] = Evidence(status=VERIFIED, quote=item.quote, page=page, note=note)
    return evidence
