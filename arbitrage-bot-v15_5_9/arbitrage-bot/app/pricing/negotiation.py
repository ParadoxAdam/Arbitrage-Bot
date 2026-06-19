"""
Negotiation / target-buy-price math (v15.5.9).

Pure functions, fully unit-testable. Given a listing's price, shipping,
expected resale, and the threshold pair (min_profit, min_roi), inverts
the profit formula to compute the maximum purchase price that would
still satisfy each threshold — and therefore the discount needed to
make the listing review-grade or alert-grade.

This module is purely additive. It reads no DB state, mutates nothing,
and is decoupled from the valuation engine. Any caller that already has
(price, shipping, expected_resale) can use it.

Profit formula being inverted (matches `pricing.profit.calculate_profit`):

    purchase       = listing.price
    inbound        = listing.shipping
    outbound       = settings.default_outbound_shipping
    estimated_tax  = purchase * sales_tax_pct
    resale_fee     = expected_resale * resale_fee_pct
    payment_fee    = expected_resale * payment_fee_pct + payment_fee_flat
    total_cost     = purchase + inbound + estimated_tax + outbound
                     + resale_fee + payment_fee
    net            = expected_resale - total_cost
    cost_basis     = purchase + inbound + estimated_tax
    roi            = net / cost_basis

Holding (expected_resale, inbound, outbound, fee structure) fixed and
treating purchase as the unknown:

    Let t = 1 + sales_tax_pct
        F = inbound + outbound + resale_fee + payment_fee   (constant in purchase)
        net = expected_resale - purchase * t - F
        cost_basis = purchase * t + inbound

    Profit constraint:  net >= min_profit
        => purchase <= (expected_resale - F - min_profit) / t

    ROI constraint:  net / cost_basis >= min_roi
        => purchase <= (expected_resale - F - min_roi * inbound)
                       / (t * (1 + min_roi))

The binding constraint is min(max_buy_for_profit, max_buy_for_roi).
A negative result means no non-negative purchase price can satisfy
that threshold — i.e. the constraint is infeasible at this resale.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional

from ..config import settings


# ── Failure-bucket / negotiation labels ──────────────────────────────

NEG_ALREADY_PASSES = "already_passes"
NEG_NEGOTIABLE = "negotiable"
NEG_TOO_EXPENSIVE = "too_expensive"
NEG_INFEASIBLE = "infeasible"

ALL_NEG_LABELS = (
    NEG_ALREADY_PASSES, NEG_NEGOTIABLE, NEG_TOO_EXPENSIVE, NEG_INFEASIBLE,
)

# Failure-bucket keys (used in summary analytics)
BUCKET_PROFITABLE_BEFORE_FEES = "profitable_before_fees"
BUCKET_FAILED_ONLY_BY_PROFIT = "failed_only_by_profit"
BUCKET_FAILED_ONLY_BY_ROI = "failed_only_by_roi"
BUCKET_FAILED_CONDITION_RISK = "failed_condition_risk"
BUCKET_FAILED_VALUATION_UNCERTAINTY = "failed_valuation_uncertainty"
BUCKET_NEGOTIABLE_REVIEW = "negotiable_review"
BUCKET_NEGOTIABLE_ALERT = "negotiable_alert"

ALL_BUCKETS = (
    BUCKET_PROFITABLE_BEFORE_FEES,
    BUCKET_FAILED_ONLY_BY_PROFIT,
    BUCKET_FAILED_ONLY_BY_ROI,
    BUCKET_FAILED_CONDITION_RISK,
    BUCKET_FAILED_VALUATION_UNCERTAINTY,
    BUCKET_NEGOTIABLE_REVIEW,
    BUCKET_NEGOTIABLE_ALERT,
)


# ── Core target-buy-price math ───────────────────────────────────────

@dataclass
class TargetBuyPrice:
    """
    Result of inverting the profit formula for one threshold pair.

    All currency values are in the same currency as `settings.currency`
    (default GBP). All ROI/percent values are expressed as fractions
    (0.20 == 20%), matching the rest of the codebase.

    None for max_buy_* means the constraint is infeasible at the given
    expected_resale — i.e. even a free purchase wouldn't satisfy it.
    """
    # Inputs (echoed back for UI convenience)
    current_price: float
    current_shipping: float
    expected_resale: float
    min_profit_threshold: float
    min_roi_threshold: float

    # Outputs
    current_landed_cost: float
    current_net_profit: float
    current_roi: float

    max_buy_for_profit: Optional[float]
    max_buy_for_roi: Optional[float]
    max_buy_overall: Optional[float]

    binding_constraint: str  # "profit" | "roi" | "both" | "infeasible"

    discount_needed_abs: Optional[float]   # current_price - max_buy_overall
    discount_needed_pct: Optional[float]

    label: str               # one of ALL_NEG_LABELS

    def to_dict(self) -> dict:
        return asdict(self)


def _profit_components(
    purchase: float,
    inbound: float,
    expected_resale: float,
    sales_tax_pct: float,
    outbound_shipping: Optional[float],
) -> tuple[float, float, float, float]:
    """
    Return (t, F_const, current_net, current_roi) for the profit formula.

    F_const = inbound + outbound + resale_fee + payment_fee
              (everything that does NOT depend on purchase).
    """
    t = 1.0 + sales_tax_pct
    outbound = (
        outbound_shipping
        if outbound_shipping is not None
        else settings.default_outbound_shipping
    )
    resale_fee = round(expected_resale * settings.resale_fee_pct, 2)
    payment_fee = round(
        expected_resale * settings.payment_fee_pct + settings.payment_fee_flat,
        2,
    )
    f_const = inbound + outbound + resale_fee + payment_fee

    estimated_tax = round(purchase * sales_tax_pct, 2)
    total_cost = (
        purchase + inbound + estimated_tax + outbound + resale_fee + payment_fee
    )
    net = expected_resale - total_cost
    cost_basis = purchase + inbound + estimated_tax
    roi = (net / cost_basis) if cost_basis > 0 else 0.0
    return t, f_const, net, roi


def target_buy_price(
    *,
    price: float,
    shipping: float,
    expected_resale: float,
    min_profit: float,
    min_roi: float,
    sales_tax_pct: float = 0.0,
    outbound_shipping: Optional[float] = None,
    negotiable_max_pct: Optional[float] = None,
    negotiable_max_abs: Optional[float] = None,
) -> TargetBuyPrice:
    """
    Compute the maximum purchase price that satisfies (min_profit, min_roi)
    given a fixed `expected_resale`. Returns a TargetBuyPrice with both
    individual constraints, the binding one, and discount/label info.

    `negotiable_max_pct` and `negotiable_max_abs` default to settings.
    A discount within EITHER limit qualifies a listing as negotiable —
    the laxer of the two wins (so a £40 discount on a £100 phone is
    negotiable on % terms; a £20 discount on a £400 phone is negotiable
    on absolute terms).
    """
    if negotiable_max_pct is None:
        negotiable_max_pct = settings.negotiation_max_discount_pct
    if negotiable_max_abs is None:
        negotiable_max_abs = settings.negotiation_max_discount_abs

    inbound = shipping or 0.0
    t, f_const, current_net, current_roi = _profit_components(
        purchase=price,
        inbound=inbound,
        expected_resale=expected_resale,
        sales_tax_pct=sales_tax_pct,
        outbound_shipping=outbound_shipping,
    )

    # Profit-bound max buy
    max_for_profit_raw = (expected_resale - f_const - min_profit) / t if t > 0 else 0.0
    max_for_profit: Optional[float] = (
        round(max_for_profit_raw, 2) if max_for_profit_raw >= 0 else None
    )

    # ROI-bound max buy
    denom = t * (1.0 + min_roi)
    if denom <= 0:
        max_for_roi_raw = 0.0
    else:
        max_for_roi_raw = (
            expected_resale - f_const - min_roi * inbound
        ) / denom
    max_for_roi: Optional[float] = (
        round(max_for_roi_raw, 2) if max_for_roi_raw >= 0 else None
    )

    # Binding constraint. To pass review BOTH constraints must hold,
    # so if either is infeasible the listing as a whole is infeasible —
    # there's no purchase price at which both thresholds are satisfied.
    if max_for_profit is None and max_for_roi is None:
        max_overall: Optional[float] = None
        binding = "infeasible"
    elif max_for_profit is None:
        # Profit constraint can't be satisfied at any purchase price
        max_overall = None
        binding = "profit"
    elif max_for_roi is None:
        max_overall = None
        binding = "roi"
    else:
        if abs(max_for_profit - max_for_roi) < 0.01:
            max_overall = round(min(max_for_profit, max_for_roi), 2)
            binding = "both"
        elif max_for_profit < max_for_roi:
            max_overall = max_for_profit
            binding = "profit"
        else:
            max_overall = max_for_roi
            binding = "roi"

    # Discount + label
    if max_overall is None:
        discount_abs: Optional[float] = None
        discount_pct: Optional[float] = None
        label = NEG_INFEASIBLE
    elif max_overall >= price - 0.005:
        # Already at or above the threshold price
        discount_abs = 0.0
        discount_pct = 0.0
        label = NEG_ALREADY_PASSES
    else:
        discount_abs = round(price - max_overall, 2)
        discount_pct = round(discount_abs / price, 4) if price > 0 else None
        # Negotiable if EITHER limit is met (whichever is laxer)
        within_pct = (
            discount_pct is not None and discount_pct <= negotiable_max_pct + 1e-9
        )
        within_abs = discount_abs <= negotiable_max_abs + 1e-9
        label = NEG_NEGOTIABLE if (within_pct or within_abs) else NEG_TOO_EXPENSIVE

    return TargetBuyPrice(
        current_price=round(price, 2),
        current_shipping=round(inbound, 2),
        expected_resale=round(expected_resale, 2),
        min_profit_threshold=min_profit,
        min_roi_threshold=min_roi,
        current_landed_cost=round(price + inbound, 2),
        current_net_profit=round(current_net, 2),
        current_roi=round(current_roi, 4),
        max_buy_for_profit=max_for_profit,
        max_buy_for_roi=max_for_roi,
        max_buy_overall=max_overall,
        binding_constraint=binding,
        discount_needed_abs=discount_abs,
        discount_needed_pct=discount_pct,
        label=label,
    )


# ── Failure bucketing ────────────────────────────────────────────────

# Failure-reason codes that count as a "hard" gating failure (other than
# profit/ROI). Used to detect "failed only by profit" / "failed only by ROI".
# We DO NOT include FAIL_ACTIVE_ONLY or FAIL_BATTERY_HEALTH here because
# those are soft signals that don't gate inclusion on their own.
_HARD_NON_MARGIN_FAILURES = frozenset({
    "score_below_threshold",
    "confidence_below_threshold",
    "match_quality_below_threshold",
    "critical_risk_flags",
    "comp_pool_rejected",
    "no_comps_found",
})

# Risk flags that always count as a condition/risk failure
_CONDITION_RISK_FLAGS = frozenset({
    "possible_damage",
    "low_battery_health",
    "iphone_locked",
    "icloud_locked",
})

# Default valuation-confidence threshold for "uncertain" bucket
DEFAULT_UNCERTAIN_VAL_CONF = 0.40


def categorize_failure(
    *,
    failure_reasons: list[str],
    risk_flags: list[str],
    price: float,
    shipping: float,
    expected_resale: float,
    valuation_confidence: Optional[float] = None,
    valuation_warnings: Optional[list[str]] = None,
    target_review: TargetBuyPrice,
    target_alert: Optional[TargetBuyPrice] = None,
) -> dict[str, bool]:
    """
    Classify a failed-listing into the analytics buckets.

    A listing can be in MULTIPLE buckets (these are NOT mutually
    exclusive). E.g. a listing can simultaneously be "profitable before
    fees" and "negotiable for review" — that's exactly the most
    interesting kind of opportunity to surface.
    """
    failure_set = set(failure_reasons or [])
    flag_set = set(risk_flags or [])
    warnings = set(valuation_warnings or [])

    # 1. Profitable before fees but not after
    landed = price + (shipping or 0.0)
    profitable_before_fees = (
        expected_resale > landed
        and target_review.current_net_profit < target_review.min_profit_threshold
    )

    # 2. Failed only by profit
    failed_only_by_profit = (
        "profit_below_threshold" in failure_set
        and "roi_below_threshold" not in failure_set
        and not (failure_set & _HARD_NON_MARGIN_FAILURES)
    )

    # 3. Failed only by ROI
    failed_only_by_roi = (
        "roi_below_threshold" in failure_set
        and "profit_below_threshold" not in failure_set
        and not (failure_set & _HARD_NON_MARGIN_FAILURES)
    )

    # 4. Condition / risk failure
    failed_condition_risk = (
        "critical_risk_flags" in failure_set
        or bool(flag_set & _CONDITION_RISK_FLAGS)
    )

    # 5. Valuation uncertainty
    has_low_conf = (
        valuation_confidence is not None
        and valuation_confidence < DEFAULT_UNCERTAIN_VAL_CONF
    )
    failed_valuation_uncertainty = (
        has_low_conf
        or "no_comps_found" in failure_set
        or "comp_pool_rejected" in failure_set
        or bool(warnings & {
            "wide_active_spread",
            "low_active_sample_size",
            "anchor_floor_disabled_for_defects",
        })
    )

    # 6. Negotiable buckets
    negotiable_review = target_review.label == NEG_NEGOTIABLE
    negotiable_alert = (
        target_alert is not None and target_alert.label == NEG_NEGOTIABLE
    )

    return {
        BUCKET_PROFITABLE_BEFORE_FEES: profitable_before_fees,
        BUCKET_FAILED_ONLY_BY_PROFIT: failed_only_by_profit,
        BUCKET_FAILED_ONLY_BY_ROI: failed_only_by_roi,
        BUCKET_FAILED_CONDITION_RISK: failed_condition_risk,
        BUCKET_FAILED_VALUATION_UNCERTAINTY: failed_valuation_uncertainty,
        BUCKET_NEGOTIABLE_REVIEW: negotiable_review,
        BUCKET_NEGOTIABLE_ALERT: negotiable_alert,
    }


# ── High-level "give me everything" helper ───────────────────────────

def analyze(
    *,
    price: float,
    shipping: float,
    expected_resale: float,
    failure_reasons: Optional[list[str]] = None,
    risk_flags: Optional[list[str]] = None,
    valuation_confidence: Optional[float] = None,
    valuation_warnings: Optional[list[str]] = None,
    sales_tax_pct: float = 0.0,
    outbound_shipping: Optional[float] = None,
) -> dict:
    """
    Convenience wrapper: returns a dict with `target_review`,
    `target_alert`, and `buckets`. Used by the API serialisers.

    The output shape is intentionally JSON-friendly so the dashboard
    can render it without further transformation.
    """
    target_review = target_buy_price(
        price=price,
        shipping=shipping,
        expected_resale=expected_resale,
        min_profit=settings.review_min_profit,
        min_roi=settings.review_min_roi,
        sales_tax_pct=sales_tax_pct,
        outbound_shipping=outbound_shipping,
    )
    target_alert = target_buy_price(
        price=price,
        shipping=shipping,
        expected_resale=expected_resale,
        min_profit=settings.min_profit,
        min_roi=settings.min_roi,
        sales_tax_pct=sales_tax_pct,
        outbound_shipping=outbound_shipping,
    )
    buckets = categorize_failure(
        failure_reasons=failure_reasons or [],
        risk_flags=risk_flags or [],
        price=price,
        shipping=shipping,
        expected_resale=expected_resale,
        valuation_confidence=valuation_confidence,
        valuation_warnings=valuation_warnings,
        target_review=target_review,
        target_alert=target_alert,
    )
    return {
        "target_review": target_review.to_dict(),
        "target_alert": target_alert.to_dict(),
        "buckets": buckets,
    }
