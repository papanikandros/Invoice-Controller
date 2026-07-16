from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Position descriptions are a short NAME (product/service + type), not the full scope prose.
# The LLM is prompted to keep them concise; this is the hard backstop so a verbose name can
# never widen the Kostenaufstellung's Beschreibung column. Truncation prefers a word boundary.
MAX_DESCRIPTION_LEN = 80


def _truncate_name(text: str, limit: int = MAX_DESCRIPTION_LEN) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    boundary = cut.rfind(" ")
    if boundary >= limit // 2:
        cut = cut[:boundary].rstrip()
    return f"{cut}…"


class Kostenkategorie(str, Enum):
    INVESTITIONSKOSTEN = "investitionskosten"
    NEBENKOSTEN = "nebenkosten"
    NACHLASS = "nachlass"


class DocumentKind(str, Enum):
    """What kind of cost document this is.

    OFFER — a vendor offer (Angebot) with a structured position table and a stated
    Nettosumme/Gesamtpreis that the cross-sum check reconciles against.

    STATEMENT — a client-issued statement / cost estimate (e.g. a 'Stellungnahme' listing
    'Kosten … für die noch kein Angebot vorliegt'). It has no vendor offer number and no
    document-stated grand total: the sum of the listed lines *is* the total, so there is
    nothing internal to reconcile against. The correctness guarantee for these rests on the
    consultant's review (the cross-sum check reports 'not applicable', not 'failed')."""

    OFFER = "offer"
    STATEMENT = "statement"


class Position(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    pos: str
    artikelnummer: str | None = None
    description: str
    qty: Decimal | None = None
    unit: str | None = None
    rabatt_pct: Decimal | None = None
    unit_price_net: Decimal | None = None
    # Optional positions (Optionalposition / Eventualposition / Mehrpreis / parenthesized
    # prices) may carry only a unit price or a per-unit rate with no computable line total,
    # so this is nullable. A mandatory position must always have a line total — enforced below.
    line_total_net: Decimal | None = None
    optional: bool = False
    optional_reason: str | None = Field(
        default=None,
        description="German marker that made this an optional position, e.g. 'optionale Position', 'Eventualposition', 'Mehrpreis', 'Preis in Klammern'.",
    )
    kategorie: Kostenkategorie = Kostenkategorie.INVESTITIONSKOSTEN
    kategorie_confidence: float | None = None
    kategorie_reason: str | None = None
    source_page: int | None = None

    @field_validator("description")
    @classmethod
    def _cap_description(cls, v: str) -> str:
        return _truncate_name(v)

    @model_validator(mode="after")
    def _mandatory_needs_total(self) -> Position:
        # The whole extraction pipeline (cross-sum, column sums) relies on every binding
        # position having a line total. Only optional positions may omit it.
        if not self.optional and self.line_total_net is None:
            raise ValueError("a non-optional position must have a line_total_net")
        return self


class OfferHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor_name: str
    vendor_short: str | None = None
    offer_number: str
    offer_date: date
    customer_name: str | None = None
    customer_address: str | None = None
    title: str | None = Field(
        default=None,
        description="Short subject like 'Kälteanlage' or 'Netzanschluss' — used in the SOLL header line",
    )


class OfferTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nettosumme: Decimal | None = None
    mwst_pct: Decimal | None = None
    mwst_amount: Decimal | None = None
    endbetrag: Decimal | None = None
    sonderpreis: Decimal | None = Field(
        default=None,
        description="Negotiated final price if different from Σ(positions). Difference becomes a Nachlass row.",
    )
    preisnachlass: Decimal | None = Field(
        default=None,
        description="Document-level discount applied to the whole offer (e.g. 'Preisnachlass netto EUR: -20.260,00'). Recorded as a positive amount; the consultant decides downstream how to apply.",
    )


class CostNarrative(BaseModel):
    """LLM-written prose summary of an offer's scope, split into investment and ancillary
    cost descriptions with the source pages they appear on. The item enumerations are German
    noun-phrase fragments (e.g. 'das Roh- und Fertigmaterialhandling, die Dosierung & Zubehör,
    … sowie das Unterwassergranuliersystem'); the surrounding sentence frame, euro amount, and
    '(s. Anlage …)' citation are assembled deterministically in code, not by the LLM."""

    model_config = ConfigDict(extra="forbid")

    investitionskosten_items: str = ""
    investitionskosten_seiten: list[int] = Field(default_factory=list)
    nebenkosten_items: str = ""
    nebenkosten_seiten: list[int] = Field(default_factory=list)


class CrossSumCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected: Decimal | None
    actual: Decimal  # Σ of mandatory (non-optional) positions — the binding total
    actual_incl_optional: Decimal | None = None  # Σ including priced optional positions
    tolerance: Decimal = Decimal("0.02")
    passed: bool
    # A statement / cost estimate has no document-stated total to reconcile against, so the
    # check is neither passed nor failed — it does not apply. `actual` still carries Σ(positions)
    # as the figure the consultant must verify by hand. `passed` stays False (nothing was
    # reconciled); consumers must check `not_applicable` first.
    not_applicable: bool = False
    message: str | None = None


class OfferDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: Path
    header: OfferHeader
    positions: list[Position]
    totals: OfferTotals
    cross_sum: CrossSumCheck
    kind: DocumentKind = DocumentKind.OFFER
    # Set for STATEMENT documents that quote bare 'Euro' amounts with no VAT breakdown: the
    # amounts are treated as netto (the funding basis) but the unstated VAT status is recorded
    # so it surfaces in the consultant's review rather than being silently assumed.
    vat_basis_unstated: bool = False
    narrative: CostNarrative | None = None
    extraction_method: str = "pdfplumber+llm"

    @property
    def positions_sum(self) -> Decimal:
        """Σ of all priced positions (mandatory + optional). Positions without a line
        total (pure rate-card optionals) contribute nothing."""
        return sum((p.line_total_net for p in self.positions if p.line_total_net is not None), Decimal(0))

    @property
    def mandatory_positions_sum(self) -> Decimal:
        return sum(
            (p.line_total_net for p in self.positions if not p.optional and p.line_total_net is not None),
            Decimal(0),
        )
