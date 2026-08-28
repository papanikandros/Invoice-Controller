from __future__ import annotations

from decimal import Decimal

from invoice_controller.models import (
    CrossSumCheck,
    InvoicePosition,
    OfferTotals,
    Position,
)


def check_invoice_positions(
    positions: list[InvoicePosition],
    netto: Decimal,
    *,
    cumulative_netto: Decimal | None = None,
    brutto: Decimal | None = None,
    tolerance: Decimal = Decimal("0.02"),
) -> CrossSumCheck:
    """Per-invoice position cross-sum (decided 2026-08-28, applied to EVERY invoice —
    EEW and BEG): Σ(extracted line items) must reconcile with the invoice's own stated
    net total, or the invoice is flagged. This is the accuracy guardrail for the
    OCR/vision path — a misread digit or a dropped line breaks the sum.

    The target is type-aware: a Schlussrechnung's positions typically describe the
    CUMULATIVE work while its `netto` is the remaining amount after deducted advances,
    so when `cumulative_netto` is stated, Σ(positions) may match EITHER figure. A Σ
    that instead matches the stated brutto is called out explicitly — positions were
    extracted gross, which the consultant must know."""
    if not positions:
        return CrossSumCheck(
            expected=netto,
            actual=Decimal(0),
            tolerance=tolerance,
            passed=False,
            message="keine Positionen extrahiert — Zeilensummen-Abgleich nicht möglich",
        )

    total = sum((p.line_total_net for p in positions), Decimal(0))
    targets: list[tuple[str, Decimal]] = [("Netto", netto)]
    if cumulative_netto is not None:
        targets.append(("Gesamt-Netto (Schlussrechnung)", cumulative_netto))

    best_label, best_target = min(targets, key=lambda t: abs(total - t[1]))
    diff = abs(total - best_target)
    if diff <= tolerance:
        return CrossSumCheck(
            expected=best_target, actual=total, tolerance=tolerance, passed=True
        )

    message = (
        f"Σ Positionen = {total} weicht von {best_label} lt. Dokument = {best_target} "
        f"um {diff} ab"
    )
    if brutto is not None and abs(total - brutto) <= tolerance:
        message += " — Σ entspricht dem BRUTTO-Betrag: Positionen wurden brutto erfasst"
    return CrossSumCheck(
        expected=best_target, actual=total, tolerance=tolerance, passed=False, message=message
    )


def check_statement(
    positions: list[Position],
    tolerance: Decimal = Decimal("0.02"),
) -> CrossSumCheck:
    """The 'cross-sum' for a client statement / cost estimate (Stellungnahme).

    A statement lists assumed costs for which no offer exists yet; it has NO document-stated
    grand total, so there is nothing to reconcile Σ(positions) against. The check therefore
    reports `not_applicable` rather than passed/failed — the correctness guarantee for these
    documents rests on the consultant's line-by-line review, not on an internal redundancy.
    `actual` carries Σ(listed lines) as the figure the consultant verifies by hand.
    """
    total = sum(
        (p.line_total_net for p in positions if p.line_total_net is not None),
        Decimal(0),
    )
    return CrossSumCheck(
        expected=None,
        actual=total,
        tolerance=tolerance,
        passed=False,
        not_applicable=True,
        message=(
            "Stellungnahme/Schätzung — kein Dokument-Gesamtbetrag zum Abgleich; "
            f"Σ der gelisteten Positionen = {total} ist maßgeblich und manuell zu prüfen"
        ),
    )


def check_offer(
    positions: list[Position],
    totals: OfferTotals,
    tolerance: Decimal = Decimal("0.02"),
) -> CrossSumCheck:
    """Reconcile extracted positions against the document's stated Nettosumme.

    Optional positions (Optionalposition / Eventualposition / Mehrpreis / parenthesized
    prices) may be inside or outside the document's stated net total — the offers are not
    consistent about it (e.g. some offers fold Mehrpreis lines into the Gesamtpreis, while
    another leaves an 'optionale Position' with an empty Betrag outside the sum). So the
    check passes if the stated Nettosumme reconciles to EITHER the mandatory-only sum OR
    the sum including priced optionals. Either match confirms faithful extraction; a miss
    on both means a position was dropped, double-counted, or a subtotal was mis-captured.
    """
    mandatory = sum(
        (p.line_total_net for p in positions if not p.optional and p.line_total_net is not None),
        Decimal(0),
    )
    with_optional = sum(
        (p.line_total_net for p in positions if p.line_total_net is not None),
        Decimal(0),
    )
    has_optional = with_optional != mandatory
    expected = totals.nettosumme
    if expected is None:
        return CrossSumCheck(
            expected=None,
            actual=mandatory,
            actual_incl_optional=with_optional if has_optional else None,
            tolerance=tolerance,
            passed=False,
            message="no Nettosumme found in document",
        )

    diff_mandatory = abs(mandatory - expected)
    diff_with_optional = abs(with_optional - expected)
    passed = min(diff_mandatory, diff_with_optional) <= tolerance

    message: str | None = None
    if not passed:
        if has_optional:
            message = (
                f"Sigma(mandatory)={mandatory} (diff {diff_mandatory}) and "
                f"Sigma(incl. optional)={with_optional} (diff {diff_with_optional}) "
                f"both differ from Nettosumme={expected}"
            )
        else:
            message = f"Sigma(positions)={mandatory} differs from Nettosumme={expected} by {diff_mandatory}"

    return CrossSumCheck(
        expected=expected,
        actual=mandatory,
        actual_incl_optional=with_optional if has_optional else None,
        tolerance=tolerance,
        passed=passed,
        message=message,
    )
