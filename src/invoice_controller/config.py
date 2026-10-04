"""projekt.yaml — the deliberately-small vne-generation project configuration.

v1 scope (decided 2026-08-24): client name+address, Bewilligungszeitraum, and the
Bescheid figures for the Förderbetrag block. Nothing else — beantragt figures are
derived from cost-estimation, discounts/ratios always follow the cost-estimation result, and document
selection is handled by classification, not exclude lists.

A missing projekt.yaml is not an error: vne-generation runs with the affected outputs degraded
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
    # Phase 2c V2 (consultant rules, 2026-10-04): the two date checks that decide
    # fundability. Auftrag erteilt must not precede the Antragstellung; the Rechnung
    # must not precede the Zuwendungsbescheid — unless an AavM (vorzeitiger
    # Maßnahmenbeginn) was granted, then its Genehmigung date is the lower bound.
    antragstellung: date | None = None
    aavm_genehmigung: date | None = None
    bescheid_eingegangen: date | None = None
    bescheid_datiert: date | None = None
    # Header identifiers of the VNE-Maske (Bescheid-extracted and/or typed).
    kennung: str | None = None
    passwort: str | None = None
    iban: str | None = None
    steuernummer: str | None = None

    @property
    def rechnung_untergrenze(self) -> date | None:
        return self.aavm_genehmigung or self.bescheid_datiert

    def auftrag_ok(self, order_date: date | None) -> bool | None:
        """None when either date is unknown — nobody can validate what no document
        states (consultant decision Q7, 2026-10-04); False is a funding-killer."""
        if order_date is None or self.antragstellung is None:
            return None
        return order_date >= self.antragstellung

    def rechnung_ok(self, invoice_date: date) -> bool | None:
        bound = self.rechnung_untergrenze
        if bound is None:
            return None
        return invoice_date >= bound

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
