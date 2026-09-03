"""R6 — invoice↔offer position matching: the deterministic multi-signal scorer.

PLAN.md §"Matching algorithm": per vendor, each invoice position is scored against
every offer position on article number, description similarity, price proximity,
and quantity; section context is deferred (v1 has no reliable section model).
Many-to-many by design: several invoice positions (Anzahlung + Schlussrechnung,
split deliveries) may map onto one offer line — aggregation happens in the
Kontrollmappe builder, not here.

Every score is a PROPOSAL. Nothing here decides — the consultant confirms in the
Abgleich sheet (Q1, 2026-09-03: any variance ≠ 0 renders red).

The LLM augmentation for unmatched/low-confidence leftovers lives in match/llm.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from invoice_controller.models import InvoicePosition, Position

# Empirical starting weights (PLAN: "tuned empirically against the corpus") — the
# corpus has no matching ground truth yet, so these encode the signal reliability
# order: an article-number hit is near-proof, price agreement is strong, wording
# drifts (weight low), quantities repeat too often to carry much weight.
W_ARTICLE = Decimal("0.35")
W_DESCRIPTION = Decimal("0.30")
W_PRICE = Decimal("0.25")
W_QTY = Decimal("0.10")

# 0.55 is reachable by description-perfect + price-exact alone (0.30 + 0.25) — the
# ZePa live run showed those pairs are correct and deserve HIGH (2026-09-03).
HIGH_THRESHOLD = Decimal("0.55")
MID_THRESHOLD = Decimal("0.40")

_ARTICLE_RE = re.compile(r"\b(?=[A-Za-z0-9./-]*\d)[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)+\b")
_STOPWORDS = {
    "und", "mit", "für", "fuer", "der", "die", "das", "inkl", "inklusive", "gemaess",
    "gemäß", "nach", "aus", "von", "bis", "je", "pro", "stk", "stück", "stueck", "pauschal",
}


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    NONE = "none"


@dataclass
class MatchProposal:
    invoice_index: int          # index into the invoice-position list
    offer_index: int | None     # None = no candidate above MID_THRESHOLD
    score: Decimal
    confidence: Confidence
    signals: dict[str, Decimal]  # per-signal contribution, for the audit trail


def _tokens(text: str) -> set[str]:
    norm = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return {t for t in re.split(r"[^a-z0-9]+", norm) if len(t) > 2 and t not in _STOPWORDS}


def _article_numbers(text: str) -> set[str]:
    return {m.group(0).lower() for m in _ARTICLE_RE.finditer(text)}


def _token_matches(t: str, others: set[str]) -> bool:
    """German compounds drift between offer and invoice ('Schraubenverdichter' vs
    'Verdichter') — containment of a ≥4-char token counts as a match."""
    if t in others:
        return True
    return len(t) >= 4 and any(
        (t in u or u in t) and len(u) >= 4 for u in others
    )


def _description_similarity(a: str, b: str) -> Decimal:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return Decimal(0)
    cover_a = Decimal(sum(1 for t in ta if _token_matches(t, tb))) / Decimal(len(ta))
    cover_b = Decimal(sum(1 for t in tb if _token_matches(t, ta))) / Decimal(len(tb))
    return (cover_a + cover_b) / 2


def _price_proximity(offer: Decimal | None, invoice: Decimal) -> Decimal:
    """1.0 at exact equality, decaying linearly to 0 at 50 % deviation. Also probes
    the invoice amount as a PARTIAL of the offer line (Anzahlung/Teilrechnung):
    a clean fraction (½, ⅓, ¼, 30 %…) of the offer price scores 0.6."""
    if offer is None or offer == 0:
        return Decimal(0)
    delta = abs(invoice - offer) / abs(offer)
    if delta <= Decimal("0.5"):
        direct = Decimal(1) - delta * 2
    else:
        direct = Decimal(0)
    partial = Decimal(0)
    if 0 < invoice < offer:
        ratio = invoice / offer
        for frac in (Decimal("0.5"), Decimal(1) / 3, Decimal("0.25"), Decimal("0.3"),
                     Decimal("0.4"), Decimal("0.2"), Decimal("0.1"), Decimal("0.9"),
                     Decimal("0.8"), Decimal("0.7")):
            if abs(ratio - frac) < Decimal("0.01"):
                partial = Decimal("0.6")
                break
    return max(direct, partial)


def score_pair(offer_pos: Position, inv_pos: InvoicePosition) -> tuple[Decimal, dict[str, Decimal]]:
    offer_text = f"{offer_pos.artikelnummer or ''} {offer_pos.description}"
    inv_text = inv_pos.description

    art_offer, art_inv = _article_numbers(offer_text), _article_numbers(inv_text)
    article = Decimal(1) if art_offer and art_inv and (art_offer & art_inv) else Decimal(0)

    description = _description_similarity(offer_pos.description, inv_pos.description)

    price = _price_proximity(offer_pos.line_total_net, inv_pos.line_total_net)

    qty = Decimal(0)
    if offer_pos.qty is not None and inv_pos.qty is not None and offer_pos.qty == inv_pos.qty:
        qty = Decimal(1)

    signals = {
        "article": article * W_ARTICLE,
        "description": description * W_DESCRIPTION,
        "price": price * W_PRICE,
        "qty": qty * W_QTY,
    }
    return sum(signals.values(), Decimal(0)), signals


def propose_matches(
    offer_positions: list[Position], invoice_positions: list[InvoicePosition]
) -> list[MatchProposal]:
    """Best offer candidate per invoice position (or none). Multiple invoice
    positions MAY share one offer line — that is the partial-invoicing reality,
    the Abgleich aggregates them."""
    proposals: list[MatchProposal] = []
    for i, inv_pos in enumerate(invoice_positions):
        best: tuple[Decimal, int, dict[str, Decimal]] | None = None
        for j, offer_pos in enumerate(offer_positions):
            if offer_pos.line_total_net is None and not offer_pos.optional:
                continue
            score, signals = score_pair(offer_pos, inv_pos)
            if best is None or score > best[0]:
                best = (score, j, signals)
        if best is None or best[0] < MID_THRESHOLD:
            proposals.append(MatchProposal(
                invoice_index=i, offer_index=None,
                score=best[0] if best else Decimal(0),
                confidence=Confidence.NONE,
                signals=best[2] if best else {},
            ))
            continue
        score, j, signals = best
        proposals.append(MatchProposal(
            invoice_index=i, offer_index=j, score=score,
            confidence=Confidence.HIGH if score >= HIGH_THRESHOLD else Confidence.MEDIUM,
            signals=signals,
        ))
    return proposals
