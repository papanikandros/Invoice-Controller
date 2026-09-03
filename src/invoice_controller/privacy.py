"""A1 — text-path client masking + local recipient check (design: PRIVACY.md §3).

Everything here is deterministic string work between two LLM-free facts: the
client identity (UI fields / config / deterministic Bescheid parse) and the
document text (pdfplumber/Tesseract). The LLM payload is built AFTER masking, so
the cloud model never sees the client string on the text path.

Order of operations is load-bearing: the RECIPIENT CHECK runs on the RAW text
(before masking) — it replaces prompt rule I7's LLM-extracted recipient with a
deterministic comparison, then the masked run expects the recipient fields null.

Corpus measurement (2026-09-03): client name verbatim in 72 % of text-layer docs,
variant-only in 0 % — exact-string + variant replacement covers the text path.
Scans/vision stay unmasked by design (PRIVACY.md §4): reduced exposure, never
"anonymised".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KUNDE_PLACEHOLDER = "[KUNDE]"
ADRESSE_PLACEHOLDER = "[KUNDENADRESSE]"

_LEGAL_FORMS = re.compile(
    r"\s*(gmbh\s*&\s*co\.?\s*kg|gmbh\s*&\s*cie\.?\s*kg|se\s*&\s*co\.?\s*kg|gmbh|ag|kg|ug"
    r"|e\.\s*k\.|ohg|gbr|mbh)\s*$",
    re.IGNORECASE,
)


def _umlaut_variants(s: str) -> set[str]:
    out = {s}
    out.add(s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
             .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue"))
    return out


def build_variants(name: str, address: str | None = None) -> tuple[list[str], list[str]]:
    """(name variants, address variants), each ordered longest-first so a long form
    is replaced before its own substring."""
    name = name.strip()
    names: set[str] = set()
    for base in _umlaut_variants(name):
        names.add(base)
        stripped = _LEGAL_FORMS.sub("", base).strip(" ,")
        if len(stripped) >= 4:
            names.add(stripped)

    addresses: set[str] = set()
    if address and address.strip():
        full_forms: set[str] = set()
        for base in _umlaut_variants(address.strip()):
            full_forms.add(base)
            full_forms.add(base.replace("Straße", "Str.").replace("straße", "str."))
            full_forms.add(base.replace("Str.", "Straße").replace("str.", "straße"))
        addresses |= full_forms
        # every form's parts (street line / PLZ+Ort) also occur alone
        for form in full_forms:
            for part in re.split(r"\s*,\s*", form):
                if len(part.strip()) >= 6:
                    addresses.add(part.strip())

    return (sorted(names, key=len, reverse=True),
            sorted(addresses, key=len, reverse=True))


def _replace_all(text: str, needles: list[str], placeholder: str) -> tuple[str, int]:
    hits = 0
    for needle in needles:
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        text, n = pattern.subn(placeholder, text)
        hits += n
    return text, hits


@dataclass
class MaskReport:
    pages: list[str]
    name_hits: int
    address_hits: int
    recipient_found: bool           # client name present in the RAW text (the local check)
    address_found: bool | None      # None = no address configured

    @property
    def any_hits(self) -> int:
        return self.name_hits + self.address_hits


def mask_client(pages: list[str], name: str, address: str | None = None) -> MaskReport:
    """Local recipient check on the raw text, THEN masking. Pure function."""
    name_variants, address_variants = build_variants(name, address)
    raw = "\n".join(pages).lower()
    recipient_found = any(v.lower() in raw for v in name_variants)
    address_found = (
        any(v.lower() in raw for v in address_variants) if address_variants else None
    )

    masked: list[str] = []
    name_hits = address_hits = 0
    for page in pages:
        # address first: it may contain no name tokens but must not survive partially
        page, a = _replace_all(page, address_variants, ADRESSE_PLACEHOLDER)
        page, n = _replace_all(page, name_variants, KUNDE_PLACEHOLDER)
        # legal-form residue after a shorter name variant matched ("[KUNDE] GmbH")
        page = re.sub(
            r"\[KUNDE\]\s*(GmbH\s*&\s*Co\.?\s*KG|GmbH|AG|KG|UG|e\.\s*K\.|OHG|GbR|mbH)",
            KUNDE_PLACEHOLDER, page,
        )
        masked.append(page)
        name_hits += n
        address_hits += a
    return MaskReport(
        pages=masked,
        name_hits=name_hits,
        address_hits=address_hits,
        recipient_found=recipient_found,
        address_found=address_found,
    )


# --- deterministic Empfänger parse from a BAFA Zuwendungsbescheid -----------------------

_EMPFAENGER_RE = re.compile(
    # After column reduction: the Zuwendungsempfänger block is a company-form line,
    # optionally followed by a contact-person line, then street (contains a digit),
    # then PLZ + Ort. First such block in the letter head wins.
    r"(?m)^(?P<name>[^\n]{4,80}?(?:GmbH\s*&\s*Co\.?\s*KG|GmbH|AG|KG|UG|e\.K\.|OHG|GbR|mbH))\s*\n"
    r"(?:[^\n]{2,60}\n){0,2}?"
    r"(?P<street>[^\n]{3,60}\d[^\n]{0,12})\n"
    r"(?P<ort>\d{5}\s+[^\n]{2,40})"
)


def _left_column(text: str) -> str:
    """BAFA letters print the recipient in the LEFT column with contact details to
    the right on the same layout-preserved lines — keep only the left segment."""
    lines = []
    for line in text.splitlines():
        parts = re.split(r"\s{3,}", line.strip())
        lines.append(parts[0] if parts else "")
    return "\n".join(lines)


def parse_empfaenger(text: str) -> tuple[str, str] | None:
    """LLM-free Zuwendungsempfänger extraction from Bescheid text: the first
    company-form address block that is NOT our own company — BAFA letters are
    addressed to the Bevollmächtigten (the consultant), whose block comes first
    (live finding EK4_322, 2026-09-03). None when the layout doesn't match (e.g.
    scanned Bescheide) — callers fall back to the UI fields, never guess."""
    from invoice_controller.match.vendor import is_own_company

    for m in _EMPFAENGER_RE.finditer(_left_column(text)):
        name = m.group("name").strip()
        if is_own_company(name):
            continue
        return name, f"{m.group('street').strip()}, {m.group('ort').strip()}"
    return None
