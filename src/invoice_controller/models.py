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

    @model_validator(mode="after")
    def _normalize_discount_fields(self) -> OfferTotals:
        # LLM runs occasionally deposit an end-of-table discount ("Sonderrabatt … -5.100,00")
        # into sonderpreis, or return preisnachlass with the document's minus sign. Normalize
        # so downstream consumers can rely on: preisnachlass = positive discount amount,
        # sonderpreis = positive negotiated final price (or None).
        if self.preisnachlass is not None and self.preisnachlass < 0:
            self.preisnachlass = -self.preisnachlass
        if self.sonderpreis is not None and self.sonderpreis <= 0:
            if self.sonderpreis < 0 and self.preisnachlass is None:
                self.preisnachlass = -self.sonderpreis
            self.sonderpreis = None
        return self


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


class VatStatus(str, Enum):
    """How VAT appears on an invoice. Drives the brutto/netto consistency check:
    STANDARD/REDUCED expect netto × (1+pct) = brutto; TAX_FREE and REVERSE_CHARGE
    expect brutto = netto (no German VAT charged — e.g. innergemeinschaftliche
    Lieferung, §13b UStG). UNCLEAR is flagged for the consultant, never guessed."""

    STANDARD = "standard"            # 19 %
    REDUCED = "reduced"              # 7 %
    TAX_FREE = "tax_free"            # steuerfrei (e.g. innergemeinschaftliche Lieferung)
    REVERSE_CHARGE = "reverse_charge"  # §13b UStG — Steuerschuldnerschaft des Leistungsempfängers
    UNCLEAR = "unclear"


class InvoiceType(str, Enum):
    RECHNUNG = "rechnung"                    # plain invoice
    ANZAHLUNGSRECHNUNG = "anzahlung"         # down payment at order
    TEILRECHNUNG = "teilrechnung"            # partial / Abschlagsrechnung at milestone
    SCHLUSSRECHNUNG = "schlussrechnung"      # final invoice
    GUTSCHRIFT = "gutschrift"                # credit note — amounts are negative


class AmountCheck(BaseModel):
    """The invoice-side analogue of the offer cross-sum: an internal redundancy check
    over the stated amounts (netto + MwSt = brutto; MwSt ≈ netto × pct). Failure never
    blocks the pipeline — it flags the row for the consultant, same as cost-estimation."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    message: str | None = None


class InvoicePosition(BaseModel):
    """One line item of an invoice (decided 2026-08-28: EVERY invoice — EEW and BEG alike — is extracted position-level, and Σ(positions) is cross-summed against
    the stated total). Leaner than the offer `Position`: invoices have no optional
    positions and no Kostenkategorie; a line total is always required (negative for
    discount/credit lines)."""

    model_config = ConfigDict(extra="forbid")

    pos: str = ""
    description: str
    qty: Decimal | None = None
    unit: str | None = None
    unit_price_net: Decimal | None = None
    line_total_net: Decimal
    source_page: int | None = None

    @field_validator("description")
    @classmethod
    def _cap_description(cls, v: str) -> str:
        return _truncate_name(v)


class InvoiceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: Path
    vendor_name: str
    vendor_address: str | None = None
    recipient_name: str | None = None
    recipient_address: str | None = None
    invoice_number: str
    invoice_date: date
    order_ref: str | None = Field(
        default=None, description="Auftrags-/Bestellnummer referenced by the invoice"
    )
    order_date: date | None = Field(
        default=None, description="Date of the Auftragsbestätigung/Bestellung — feeds 'Auftrag erteilt am'"
    )
    invoice_type: InvoiceType = InvoiceType.RECHNUNG
    subject: str | None = Field(
        default=None, description="Short Verwendungszweck like 'Werksverrohrung' or 'Kältemaschine'"
    )
    # The authoritative figure: the netto amount payable for THIS invoice. For a
    # Schlussrechnung this is the remaining amount after deducted Abschläge, not the
    # cumulative project total. Negative for a Gutschrift.
    netto: Decimal
    mwst_pct: Decimal | None = None
    mwst_amount: Decimal | None = None
    brutto: Decimal | None = None
    vat_status: VatStatus = VatStatus.STANDARD
    skonto_pct: Decimal | None = Field(
        default=None, description="Offered early-payment discount rate (terms, not necessarily taken)"
    )
    skonto_deadline: date | None = None
    # Schlussrechnung audit trail: the cumulative project netto and the sum of prior
    # advances the invoice deducts. `netto` above stays the own remaining amount.
    cumulative_netto: Decimal | None = None
    deducted_advances_netto: Decimal | None = None
    # Position-level extraction (2026-08-28): every invoice's line items, verified by
    # `position_check` — Σ(positions) against the stated netto (cumulative_netto for a
    # Schlussrechnung with deducted advances). The stated netto stays authoritative for
    # every downstream figure; positions are the verification layer (and the substrate
    # for BEG per-position eligibility).
    positions: list[InvoicePosition] = Field(default_factory=list)
    position_check: CrossSumCheck | None = None
    # R2 (2026-08-31): verbatim-amount grounding — stated amounts the LLM returned that
    # are NOT findable in the source text (text/Tesseract paths only). Flag, never block.
    grounding_check: AmountCheck | None = None
    amount_check: AmountCheck
    extraction_method: str = "pdfplumber+llm"


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
    # R2 (2026-08-31): verbatim-amount grounding, see InvoiceDocument.grounding_check.
    grounding_check: AmountCheck | None = None
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
