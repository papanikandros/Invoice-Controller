"""Vendor resolution: which cost-estimation block does an invoice belong to?

Per-vendor, not per-position (the confirmed vne-generation model). Matching is name-based
with legal-form stripping and token overlap — invoice letterheads and offer
headers name the same company differently ("L&R Kältetechnik GmbH & Co.KG" vs
"L&R"). An invoice from the consultant's own company is the Einsparkonzept fee
(category EK, Anteil 1) and never matches a vendor block. No match and no own-
company → the caller red-flags the row; nothing is guessed.
"""

from __future__ import annotations

import re

from invoice_controller.vne.ratios import VendorRatio

# The consultant's own company — its invoices are the Einsparkonzept fee.
_OWN_COMPANY_RE = re.compile(r"energie\s*konzept", re.IGNORECASE)

_LEGAL_FORMS = re.compile(
    r"\b(gmbh|mbh|ag|kg|ug|ohg|co|cokg|se|e\.?k\.?|inc|ltd|haftungsbeschränkt)\b\.?",
    re.IGNORECASE,
)
_NOISE = re.compile(r"[&+./,()\-–]|\bund\b|\b\d+\b", re.IGNORECASE)


def is_own_company(vendor_name: str) -> bool:
    return bool(_OWN_COMPANY_RE.search(vendor_name))


_GLUED_LEGAL_SUFFIXES = ("gmbh", "mbh", "cokg", "co", "kg", "ug", "ohg")


def _strip_glued_legal_forms(token: str) -> str:
    """PDF extraction sometimes glues names to their legal form ("RächerGmbH&Co.KG"
    → token "raechergmbhco"). Peel legal-form suffixes off long tokens so the
    distinctive name remains."""
    stripped = token
    changed = True
    while changed and len(stripped) > 5:
        changed = False
        for suffix in _GLUED_LEGAL_SUFFIXES:
            if stripped.endswith(suffix) and len(stripped) - len(suffix) >= 4:
                stripped = stripped[: -len(suffix)]
                changed = True
                break
    return stripped


def _token_list(name: str) -> list[str]:
    s = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    # Only single-letter initials collapse ("L&R" → "lr"); "GmbH & Co" must not
    # become the token "gmbhco" that the legal-form strip then reduces to "gmbh" —
    # that made every GmbH share a token (Gräfe matched the L&R block, 2026-09-23).
    s = re.sub(r"\b(\w)\s*&\s*(\w)\b", r"\1\2", s)
    s = _LEGAL_FORMS.sub(" ", s)
    s = _NOISE.sub(" ", s)
    tokens = [_strip_glued_legal_forms(t) for t in s.split()]
    return [t for t in tokens if len(t) >= 2 and t not in _GLUED_LEGAL_SUFFIXES]


def normalize_vendor(name: str) -> set[str]:
    """Reduce a company name to its distinctive lowercase tokens. Ampersand-joined
    initials ("L&R", "H&T") collapse into one token so short names survive the
    single-letter filter."""
    return set(_token_list(name))


def _compact(name: str) -> str:
    """Order-preserving concatenation of the tokens — lets 'EAGLEVIZION' meet
    'Eagle Vizion (6511660 Canada Inc.)' via substring containment."""
    return "".join(_token_list(name))


def _edit_distance_le_1(a: str, b: str) -> bool:
    """True when a and b differ by at most one substitution/insertion/deletion —
    enough for umlaut-transcription slips like 'raecher' vs 'roecher'."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if a == b[:i] + b[i + 1 :]:      # deletion from b
            return True
        if len(a) == len(b) and a[:i] + b[i] + a[i + 1 :] == b:  # substitution
            return True
    return False


def _overlap_score(inv_tokens: set[str], block_tokens: set[str]) -> float:
    """Shared-token ratio, counting near-miss tokens (edit distance ≤ 1 on tokens
    of length ≥ 5) as shared — OCR/extraction spelling slips must not unmatch a
    vendor."""
    if not inv_tokens or not block_tokens:
        return 0.0
    shared = 0
    for t in inv_tokens:
        if t in block_tokens:
            shared += 1
        elif len(t) >= 5 and any(len(b) >= 5 and _edit_distance_le_1(t, b) for b in block_tokens):
            shared += 1
    return shared / min(len(inv_tokens), len(block_tokens))


def match_vendor(invoice_vendor: str, ratios: list[VendorRatio]) -> VendorRatio | None:
    """Best-overlap match between the invoice's vendor name and the cost-estimation blocks.
    Requires shared distinctive tokens (exact, near-miss, or compact-containment) —
    a zero-overlap 'best guess' would silently misroute an invoice, so None means
    none."""
    inv_tokens = normalize_vendor(invoice_vendor)
    inv_compact = _compact(invoice_vendor)
    if not inv_tokens:
        return None
    best: VendorRatio | None = None
    best_score = 0.0
    for ratio in ratios:
        block_tokens = normalize_vendor(ratio.vendor) | normalize_vendor(ratio.header)
        score = _overlap_score(inv_tokens, block_tokens)
        # Concatenated-name containment: "eaglevizion" ⊂ "eaglevizioncanadainc".
        if score == 0.0:
            block_compact = _compact(ratio.vendor)
            if len(block_compact) >= 5 and len(inv_compact) >= 5 and (
                block_compact in inv_compact or inv_compact in block_compact
            ):
                score = 0.5
        if score > best_score:
            best, best_score = ratio, score
    return best
