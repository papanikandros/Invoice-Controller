"""Zahlungsnachweis extraction + reconciliation (B4).

BEG projects prove actual payment with bank-transfer documents — mostly PNG/JPEG
screenshots of online-banking transfers, some PDFs (SEPA receipts, Kontoauszug
pages). The invoice states only the Skonto TERMS; whether the client took the
discount, and when they paid, is provable only here (Madroch: 19.489,23 billed,
18.904,56 paid = 3 % Skonto; Holtkamp: "am … bezahlt, 3 % Skonto").

Extraction is vision-first (screenshots have no text layer); PDFs with text go the
text path. The reconciliation chain is fully deterministic (decided 2026-08-28):
payee ≈ vendor, then exact brutto | brutto × (1 − stated Skonto %) | invoice number
in the Verwendungszweck. No proof → bezahlt defaults to the Re-Betrag with a red
"kein Zahlungsnachweis" flag; an amount no rule explains is flagged, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, BinaryContent

from invoice_controller.llm.extract import (
    DETERMINISTIC_SETTINGS,
    _resolve_model,
    _run_with_http_retry,
)
from invoice_controller.match.vendor import _overlap_score, normalize_vendor
from invoice_controller.models import InvoiceDocument
from invoice_controller.normalize import format_de_decimal, normalize_text
from invoice_controller.pdf.ocr import is_text_layer_empty, render_page_pngs
from invoice_controller.pdf.text import extract_pages

_AMOUNT_TOLERANCE = Decimal("0.02")
# Skonto targets are computed (brutto × (1−pct)) — banks round the cent freely.
_SKONTO_TOLERANCE = Decimal("0.05")

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class PaymentProof(BaseModel):
    """One transfer as stated on a payment document. Everything nullable — an
    unreadable screenshot yields honest Nones, never guesses."""

    model_config = ConfigDict(extra="forbid")

    payer_name: str | None = None
    payee_name: str | None = None
    amount: Decimal | None = Field(
        default=None, description="The transferred amount as printed, positive"
    )
    payment_date: date | None = None
    verwendungszweck: str | None = None


class ExtractedPayments(BaseModel):
    """LLM output per payment document — one screenshot/page can show several
    transfers (e.g. a Kontoauszug excerpt)."""

    model_config = ConfigDict(extra="forbid")

    proofs: list[PaymentProof] = []


SYSTEM_PROMPT = """You extract bank-transfer data from German payment-proof documents (Zahlungsnachweise) for a funded-project audit: online-banking screenshots, SEPA-Überweisung receipts, Kontoauszug excerpts.

RULES:
(P1) Extract EVERY transfer visible in the document into `proofs` — a Kontoauszug excerpt can show several. One transfer = one proof entry.
(P2) Per transfer: `payer_name` (Auftraggeber/Kontoinhaber), `payee_name` (Empfänger/Begünstigter), `amount` (the transferred amount, positive), `payment_date` (Ausführungs-/Buchungs-/Wertstellungsdatum — prefer the execution date), `verwendungszweck` (the full reference text, verbatim — it often carries the invoice number).
(P3) Numbers are German format: "1.234,56" = 1234.56. Return decimal STRINGS with dot as decimal separator, no thousand separators. Dates as YYYY-MM-DD.
(P4) Return every schema key explicitly; use null for anything the document does not state. Never fabricate names, amounts, or dates. A blurry or cropped value is null.
(P5) Ignore balances (Kontostand), fees, and unrelated transactions that are clearly not vendor payments (e.g. Miete, Gehalt) — but when in doubt, extract the transfer and let the reconciliation decide."""


@lru_cache(maxsize=1)
def get_payments_agent() -> Agent[None, ExtractedPayments]:
    return Agent(
        _resolve_model(),
        output_type=ExtractedPayments,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
        model_settings=DETERMINISTIC_SETTINGS,
    )


def extract_payment_proofs(
    path: Path, agent: Agent[None, ExtractedPayments] | None = None
) -> list[PaymentProof]:
    """Vision for images and scanned PDFs, text path for PDFs with a text layer."""
    runner = agent or get_payments_agent()
    suffix = path.suffix.lower()

    if suffix in _MEDIA_TYPES:
        message: list[Any] = [
            "The following image is one German payment-proof document (Zahlungsnachweis). "
            "Extract every transfer per the rules in the system prompt.",
            BinaryContent(data=path.read_bytes(), media_type=_MEDIA_TYPES[suffix]),
        ]
        return _run_with_http_retry(runner, message).proofs

    pages = [normalize_text(p) for p in extract_pages(path)]
    if is_text_layer_empty(pages):
        message = [
            "The following page image(s) are one German payment-proof document "
            "(Zahlungsnachweis) with no text layer. Extract every transfer per the "
            "rules in the system prompt.",
        ]
        for png in render_page_pngs(path):
            message.append(BinaryContent(data=png, media_type="image/png"))
        return _run_with_http_retry(runner, message).proofs

    paginated = "\n".join(f"<<PAGE {i}>>\n{p}" for i, p in enumerate(pages, start=1))
    return _run_with_http_retry(
        runner,
        "Below is the text of one German payment-proof document (Zahlungsnachweis). "
        f"Extract every transfer per the rules in the system prompt.\n\n{paginated}",
    ).proofs


# --- reconciliation ---------------------------------------------------------------------


class PaymentStatus(str, Enum):
    PAID = "paid"                        # exact brutto found
    PAID_SKONTO = "paid_skonto"          # brutto × (1 − stated Skonto %) found
    AMOUNT_MISMATCH = "amount_mismatch"  # proof tied to the invoice, amount unexplained
    NO_PROOF = "no_proof"                # nothing matched — bezahlt defaults to Re-Betrag


@dataclass
class ProofRecord:
    """One extracted transfer with its provenance, as reconciliation input."""

    source_path: Path
    proof: PaymentProof
    consumed: bool = False


@dataclass
class Reconciliation:
    """Per-invoice payment result — feeds the `bezahlt` + Info/Anmerkung columns."""

    invoice: InvoiceDocument
    status: PaymentStatus
    bezahlt: Decimal | None = None       # the amount to record (Re-Betrag on NO_PROOF is B5's default)
    paid_date: date | None = None
    skonto_pct_taken: Decimal | None = None
    proof_paths: list[Path] = field(default_factory=list)
    note: str | None = None              # German, for the Info/Anmerkung cell


def _norm_number(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _vendor_matches(proof: PaymentProof, invoice: InvoiceDocument) -> bool:
    if proof.payee_name is None:
        return False
    return (
        _overlap_score(
            normalize_vendor(proof.payee_name), normalize_vendor(invoice.vendor_name)
        )
        > 0
    )


def _renr_in_zweck(proof: PaymentProof, invoice: InvoiceDocument) -> bool:
    if proof.verwendungszweck is None:
        return False
    number = _norm_number(invoice.invoice_number)
    # Guard against trivially short numbers ("1", "23") matching any digit run.
    return len(number) >= 4 and number in _norm_number(proof.verwendungszweck)


def _amount_rule(
    proof: PaymentProof, invoice: InvoiceDocument
) -> tuple[PaymentStatus, Decimal | None] | None:
    """Which amount rule explains this proof for this invoice, if any.
    Returns (status, skonto_pct) or None when the amount matches no rule."""
    if proof.amount is None:
        return None
    target = invoice.brutto if invoice.brutto is not None else invoice.netto
    if abs(proof.amount - target) <= _AMOUNT_TOLERANCE:
        return (PaymentStatus.PAID, None)
    if invoice.skonto_pct is not None:
        skonto_target = target * (1 - invoice.skonto_pct / Decimal(100))
        if abs(proof.amount - skonto_target) <= _SKONTO_TOLERANCE:
            return (PaymentStatus.PAID_SKONTO, invoice.skonto_pct)
    return None


def reconcile_payments(
    invoices: list[InvoiceDocument], proofs: list[ProofRecord]
) -> list[Reconciliation]:
    """Deterministic matching, one proof consumed per invoice. Invoices are processed
    in date order so identical amounts pair with the earlier invoice first."""
    results: list[Reconciliation] = []

    for invoice in sorted(invoices, key=lambda i: (i.invoice_date, i.invoice_number)):
        # Candidates tied to this invoice: payee ≈ vendor, or Re-Nr. in the Zweck.
        candidates = [
            r
            for r in proofs
            if not r.consumed and (_vendor_matches(r.proof, invoice) or _renr_in_zweck(r.proof, invoice))
        ]

        # Best rule wins: exact > skonto > Re-Nr.-tied with unexplained amount.
        chosen: ProofRecord | None = None
        verdict: tuple[PaymentStatus, Decimal | None] | None = None
        for record in candidates:
            rule = _amount_rule(record.proof, invoice)
            if rule is None:
                continue
            if verdict is None or rule[0] is PaymentStatus.PAID and verdict[0] is not PaymentStatus.PAID:
                chosen, verdict = record, rule
                if rule[0] is PaymentStatus.PAID:
                    break
        if chosen is None and invoice.cumulative_netto is not None and len(candidates) >= 2:
            # Schlussrechnung pattern (Madroch Zimmerei): the project was paid across
            # several Abschlag transfers; the sheet records their Σ as bezahlt. The Σ
            # is adopted with an honest note; a Σ that matches no target is flagged.
            total = sum(
                (r.proof.amount for r in candidates if r.proof.amount is not None),
                Decimal(0),
            )
            target = invoice.brutto if invoice.brutto is not None else invoice.netto
            if invoice.mwst_pct is not None:
                cumulative_target = (
                    invoice.cumulative_netto * (1 + invoice.mwst_pct / Decimal(100))
                ).quantize(Decimal("0.01"))
            else:
                cumulative_target = invoice.cumulative_netto
            matches = any(
                abs(total - t) <= _SKONTO_TOLERANCE for t in (target, cumulative_target)
            )
            for r in candidates:
                r.consumed = True
            last_date = max(
                (r.proof.payment_date for r in candidates if r.proof.payment_date),
                default=None,
            )
            note = f"Σ aus {len(candidates)} Zahlungen"
            if not matches:
                note += (
                    f" = {format_de_decimal(total)} — weicht von Gesamt-Brutto "
                    f"{format_de_decimal(cumulative_target)} ab, prüfen"
                )
            results.append(
                Reconciliation(
                    invoice=invoice,
                    status=PaymentStatus.PAID if matches else PaymentStatus.AMOUNT_MISMATCH,
                    bezahlt=total,
                    paid_date=last_date,
                    proof_paths=[r.source_path for r in candidates],
                    note=note,
                )
            )
            continue

        if chosen is None:
            # No amount rule fired — a proof that names this Re-Nr. is still evidence,
            # but its amount is unexplained → loud flag (never silently adopted).
            renr_tied = [r for r in candidates if _renr_in_zweck(r.proof, invoice)]
            if renr_tied:
                record = renr_tied[0]
                record.consumed = True
                amount = record.proof.amount
                results.append(
                    Reconciliation(
                        invoice=invoice,
                        status=PaymentStatus.AMOUNT_MISMATCH,
                        bezahlt=amount,
                        paid_date=record.proof.payment_date,
                        proof_paths=[record.source_path],
                        note=(
                            "Zahlungsnachweis zur Re-Nr. gefunden, Betrag "
                            f"{format_de_decimal(amount) if amount is not None else '—'} "
                            "weder Brutto noch Skonto-Betrag — prüfen"
                        ),
                    )
                )
                continue
            results.append(
                Reconciliation(
                    invoice=invoice,
                    status=PaymentStatus.NO_PROOF,
                    note="kein Zahlungsnachweis",
                )
            )
            continue

        chosen.consumed = True
        status, skonto_pct = verdict
        date_txt = (
            f"am {chosen.proof.payment_date.strftime('%d.%m.%Y')} bezahlt"
            if chosen.proof.payment_date
            else "bezahlt (Datum fehlt auf dem Nachweis)"
        )
        note = date_txt if status is PaymentStatus.PAID else (
            f"{date_txt}, {format_de_decimal(skonto_pct)} % Skonto"
        )
        results.append(
            Reconciliation(
                invoice=invoice,
                status=status,
                bezahlt=chosen.proof.amount,
                paid_date=chosen.proof.payment_date,
                skonto_pct_taken=skonto_pct,
                proof_paths=[chosen.source_path],
                note=note,
            )
        )

    return results
