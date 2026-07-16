from __future__ import annotations

from decimal import Decimal

from invoice_controller.models import Kostenkategorie, OfferDocument
from invoice_controller.normalize import format_de_decimal, format_seiten


def _category_subtotal(offer: OfferDocument, kategorie: Kostenkategorie) -> Decimal:
    return sum(
        (
            p.line_total_net
            for p in offer.positions
            if p.kategorie == kategorie and p.line_total_net is not None
        ),
        Decimal(0),
    )


def _citation(offer: OfferDocument, seiten: list[int]) -> str:
    seiten_text = format_seiten(seiten)
    anlage = offer.source_path.name
    if seiten_text:
        return f"(s. Anlage {anlage}, {seiten_text})"
    return f"(s. Anlage {anlage})"


def render_narrative_blocks(offer: OfferDocument) -> tuple[str | None, str | None]:
    """Assemble the two prose blocks (Investitionskosten, Nebenkosten) from the LLM's
    item enumerations + the cost-table category subtotals + a source citation. The euro
    amount is the category subtotal (omitted when zero); a block with no items is None."""
    narrative = offer.narrative
    if narrative is None:
        return None, None

    inv_text: str | None = None
    if narrative.investitionskosten_items.strip():
        amount = _category_subtotal(offer, Kostenkategorie.INVESTITIONSKOSTEN)
        betrag = f" {format_de_decimal(amount)} €" if amount > 0 else ""
        inv_text = (
            f"Die Investitionskosten{betrag} beinhalten "
            f"{narrative.investitionskosten_items.strip()} "
            f"{_citation(offer, narrative.investitionskosten_seiten)}"
        )

    neb_text: str | None = None
    if narrative.nebenkosten_items.strip():
        amount = _category_subtotal(offer, Kostenkategorie.NEBENKOSTEN)
        betrag = f" {format_de_decimal(amount)} €" if amount > 0 else ""
        neb_text = (
            f"Die Nebenkosten{betrag} beinhalten "
            f"{narrative.nebenkosten_items.strip()} "
            f"{_citation(offer, narrative.nebenkosten_seiten)}"
        )

    return inv_text, neb_text
