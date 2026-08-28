"""projekt.yaml — the deliberately-small F2 project configuration.

v1 scope (decided 2026-08-24): client name+address, Bewilligungszeitraum, and the
Bescheid figures for the Förderbetrag block. Nothing else — beantragt figures are
derived from F1, discounts/ratios always follow the F1 result, and document
selection is handled by classification, not exclude lists.

A missing projekt.yaml is not an error: F2 runs with the affected outputs degraded
(window column blank, Förderbetrag block unfilled) so the corpus projects — which
ship no yaml — still process end-to-end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    address: str | None = None


class BescheidConfig(BaseModel):
    """Figures from the (latest) Zuwendungsbescheid / ESK — never extracted in v1."""

    model_config = ConfigDict(extra="forbid")

    foerderbetrag: Decimal | None = None
    kostendeckel_foerderanteil: Decimal | None = None  # e.g. 0.4
    agvo_referenzkosten: Decimal | None = None


class ProjektConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: ClientConfig | None = None
    bewilligungszeitraum_start: date | None = None
    bewilligungszeitraum_end: date | None = None
    bescheid: BescheidConfig | None = None

    @property
    def has_window(self) -> bool:
        return self.bewilligungszeitraum_start is not None and self.bewilligungszeitraum_end is not None

    def window_ok(self, d: date) -> bool | None:
        """None when no window is configured — the column stays blank, never guessed."""
        if not self.has_window:
            return None
        return self.bewilligungszeitraum_start <= d <= self.bewilligungszeitraum_end


def load_project_config(path: str | Path) -> ProjektConfig:
    path = Path(path)
    if not path.exists():
        return ProjektConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return ProjektConfig.model_validate(data)
