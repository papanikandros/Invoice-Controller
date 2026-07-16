from __future__ import annotations

from decimal import Decimal

from invoice_controller.models import CrossSumCheck, OfferTotals, Position


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
    consistent about it (e.g. L&R folds Mehrpreis lines into the Gesamtpreis, while a
    GranuTrack 'optionale Position' has an empty Betrag and sits outside the sum). So the
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
