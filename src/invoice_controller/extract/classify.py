"""Document classification — every file in a project folder gets a class.

Replaces manual exclude lists (decided 2026-08-24, extended for BEG 2026-08-28):
a close-out folder holds far more than offers and invoices — payment proofs,
Bescheide, application confirmations, Erklärungen, the tool's own outputs.
Classification is tiered and deterministic-first, mirroring cost-estimation's doc-kind
detection:

  1. filename patterns (free, covers the vast majority of the corpus),
  2. first-page text keywords for names that give no signal,
  3. undecidable PDFs → INVOICE, so they surface red-flagged in the table rather
     than silently binned as `other` (loud-not-silent; the extractor's own reading
     is the final backstop).

Six classes since 2026-08-28 (BEG needs the funding documents and payment proofs
as first-class inputs): offer / invoice / antragsbestaetigung / zuwendungsbescheid
/ zahlungsnachweis / other. The folder walk is recursive (BEG projects nest
`Rechnungen/`, `EKK/`, `geprüfte Rechnungen/`, per-Gewerk folders) and picks up
image files (PNG/JPEG payment-proof screenshots). Non-invoice/-offer documents are
never dropped silently — the caller reports them by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from invoice_controller.pdf.text import extract_pages


class DocClass(str, Enum):
    OFFER = "offer"
    INVOICE = "invoice"
    ANTRAGSBESTAETIGUNG = "antragsbestaetigung"   # BzA, Förderantrag, Antragsbestätigung, tpb/begpt forms
    ZUWENDUNGSBESCHEID = "zuwendungsbescheid"     # incl. KfW-Zusage, Festsetzungs-/Auszahlungsbescheid
    ZAHLUNGSNACHWEIS = "zahlungsnachweis"         # bank-transfer proofs, incl. image screenshots
    OTHER = "other"


@dataclass
class Classified:
    path: Path
    doc_class: DocClass
    reason: str


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}

# Funding-program documents — checked FIRST because the generic OTHER vocabulary
# ("bescheid", "nachweis", "antrag") would otherwise swallow them.
_ANTRAG_NAME_RE = re.compile(
    r"antragsbest|f[öo]rderantrag|bza[\s_-]|bestätigung\s+zum\s+antrag"
    r"|^tpb\d*_|^begpt(?!vn)\d*_?",  # BAFA/KfW technical project forms (Antrag side); begptvn_ = Nachweis side
    re.IGNORECASE,
)
_BESCHEID_NAME_RE = re.compile(
    r"zuwendungsbescheid|zusageschreiben|festsetzungsbescheid|auszahlungsbescheid"
    r"|änderungsbescheid|aenderungsbescheid|\bbescheid\b|bescheid[\s_.-]",
    re.IGNORECASE,
)
_ZAHLUNG_NAME_RE = re.compile(
    r"zahlungsnachweis|kontoauszug|überweisung|ueberweisung|zahlungsbeleg|umsatz",
    re.IGNORECASE,
)

# Tool artifacts and known non-invoice paperwork — checked before the invoice/offer
# tiers so e.g. "Kostenaufstellung" never falls through to the offer tier.
_OTHER_NAME_RE = re.compile(
    r"kostenaufstellung|kostenzusammenstellung|vne[-_ ]?tabelle|fragenkatalog"
    r"|standortbeschreibung|standort"
    r"|vertrag|erkl[äa]rung|leasing|vollmacht|beauftragung"
    r"|zahlplan|nachweis|b[üu]rgschaft|\bauftrag|^auf[\s_.-]|bestellung|lieferschein"
    r"|best[äa]tigung"  # order/completion confirmations ("Bestätigung AN-…", "… nach Durchführung")
    r"|verwendungsnachweis|vdz|jaz|heizlast|stundenzettel|kaufbeleg"
    r"|bestätigung\s+nach\s+durchführung"
    r"|^eewvn_|^qstvn_|^begptvn|^tpn\d*_",  # BAFA/KfW Verwendungsnachweis-side forms
    re.IGNORECASE,
)
_INVOICE_NAME_RE = re.compile(
    r"\brg[\s_.-]|^rg|rechnung|\brech\.|gutschrift|\bre[-_]\d|abschlag|schlussrechnung|anzahlung"
    r"|^\d*\.?\s*a[rz][\s_.-]|storno",  # BEG naming: "1. AR …", "2. AZ …", "Rech. …"
    re.IGNORECASE,
)
_OFFER_NAME_RE = re.compile(
    r"angebot|^ang[-_ ]?\d|\ban[-_]?\d|[-_ ]soll|stellungnahme|sch[äa]tzung"
    r"|kostenvoranschlag|eigenerkl",
    re.IGNORECASE,
)

# First-page text signals, applied only when the filename is silent. Order matters:
# funding documents first (a Bescheid quotes application data), then invoices (a
# Schlussrechnung mentions its Angebot, so invoice markers precede offer markers).
_ANTRAG_TEXT_RE = re.compile(
    r"bestätigung\s+zum\s+antrag|bza-id|antragsbestätigung|förderantrag|vorgangsnummer",
    re.IGNORECASE,
)
_BESCHEID_TEXT_RE = re.compile(
    r"zuwendungsbescheid|zusageschreiben|festsetzungsbescheid|bewilligung",
    re.IGNORECASE,
)
_ZAHLUNG_TEXT_RE = re.compile(
    r"kontoauszug|zahlungsavis|überweisungsauftrag|ausgeführte\s+überweisung",
    re.IGNORECASE,
)
_INVOICE_TEXT_RE = re.compile(
    r"\brechnung\b|\bgutschrift\b|rechnungs?-?\s*(nr|nummer)|belegnummer"
    r"|anzahlungsrechnung|abschlagsrechnung|teilrechnung|schlussrechnung",
    re.IGNORECASE,
)
_OFFER_TEXT_RE = re.compile(
    r"\bangebot\b|angebots?-?\s*(nr|nummer)|\boffer\b|kostenvoranschlag",
    re.IGNORECASE,
)
_OTHER_TEXT_RE = re.compile(
    r"leasingvertrag|kaufvertrag|auftragsbestätigung|fachunternehmererklärung",
    re.IGNORECASE,
)


def _classify_by_name(name: str) -> tuple[DocClass, str] | None:
    if _ANTRAG_NAME_RE.search(name):
        return DocClass.ANTRAGSBESTAETIGUNG, "filename"
    if _BESCHEID_NAME_RE.search(name):
        return DocClass.ZUWENDUNGSBESCHEID, "filename"
    if _ZAHLUNG_NAME_RE.search(name):
        return DocClass.ZAHLUNGSNACHWEIS, "filename"
    if _OTHER_NAME_RE.search(name) and not _INVOICE_NAME_RE.search(name):
        return DocClass.OTHER, "filename"
    if _INVOICE_NAME_RE.search(name):
        return DocClass.INVOICE, "filename"
    if _OFFER_NAME_RE.search(name):
        return DocClass.OFFER, "filename"
    return None


def classify_image(path: Path) -> Classified:
    """Image files (PNG/JPEG) are almost always payment-proof screenshots when their
    name or parent folder says so; anything else (site photos, Stundenzettel scans)
    is `other` — reported, never silently dropped."""
    haystack = f"{path.parent.name} {path.name}"
    if _ZAHLUNG_NAME_RE.search(haystack):
        return Classified(path, DocClass.ZAHLUNGSNACHWEIS, "image in Zahlungsnachweis context")
    return Classified(path, DocClass.OTHER, "image without payment-proof signal")


def classify_pdf(path: Path, first_page_text: str | None = None) -> Classified:
    """Classify one PDF. `first_page_text` may be passed to avoid re-reading a file
    the caller already extracted; None means read page 1 here (empty for scans —
    scans with silent filenames fall through to INVOICE and get resolved by the
    extractor's own reading)."""
    name = path.name

    by_name = _classify_by_name(name)
    if by_name is None and path.parent.name:
        # BEG folders carry the signal in the directory name ("Zahlungsnachweise/",
        # "geprüfte Rechnungen/") while the file itself is mute ("img20260115.pdf").
        by_name_parent = _classify_by_name(path.parent.name)
        if by_name_parent is not None and by_name_parent[0] in (
            DocClass.ZAHLUNGSNACHWEIS,
            DocClass.INVOICE,
        ):
            by_name = (by_name_parent[0], "parent folder name")
    if by_name is not None:
        return Classified(path, by_name[0], by_name[1])

    if first_page_text is None:
        try:
            pages = extract_pages(path)
            first_page_text = pages[0] if pages else ""
        except Exception:
            first_page_text = ""

    if first_page_text.strip():
        if _ANTRAG_TEXT_RE.search(first_page_text):
            return Classified(path, DocClass.ANTRAGSBESTAETIGUNG, "first-page text")
        if _BESCHEID_TEXT_RE.search(first_page_text):
            return Classified(path, DocClass.ZUWENDUNGSBESCHEID, "first-page text")
        if _ZAHLUNG_TEXT_RE.search(first_page_text):
            return Classified(path, DocClass.ZAHLUNGSNACHWEIS, "first-page text")
        # Invoice signals outrank the OTHER vocabulary: invoices routinely cite the
        # order they bill ("laut unserer Auftragsbestätigung Nr. …") — EK4_333 lost all
        # five L&R invoices to that (2026-09-23). A contract that merely says
        # "Rechnung" lands as a loud, flagged invoice instead of vanishing.
        if _INVOICE_TEXT_RE.search(first_page_text):
            return Classified(path, DocClass.INVOICE, "first-page text")
        if _OTHER_TEXT_RE.search(first_page_text):
            return Classified(path, DocClass.OTHER, "first-page text")
        if _OFFER_TEXT_RE.search(first_page_text):
            return Classified(path, DocClass.OFFER, "first-page text")

    # Undecidable (e.g. a scan with a mute filename): treat as invoice so it renders
    # red-flagged in the table for the consultant instead of vanishing into `other`.
    return Classified(path, DocClass.INVOICE, "undecided — defaulted to invoice for review")


def _is_candidate(path: Path) -> bool:
    if path.name.startswith((".", "~")) or ".~lock" in path.name:
        return False
    return path.suffix.lower() == ".pdf" or path.suffix.lower() in IMAGE_SUFFIXES


def classify_folder(project_dir: Path, *, recursive: bool = True) -> list[Classified]:
    """Walk the project folder (recursively by default — BEG projects nest their
    documents) and classify every PDF and image file."""
    it = project_dir.rglob("*") if recursive else project_dir.glob("*")
    files = sorted(p for p in it if p.is_file() and _is_candidate(p))
    results: list[Classified] = []
    for p in files:
        if p.suffix.lower() in IMAGE_SUFFIXES:
            results.append(classify_image(p))
        else:
            results.append(classify_pdf(p))
    return results
