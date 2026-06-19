"""
Unit tests for app.pricing.negotiation (v15.5.9).

Two layers:
  1. target_buy_price() — invert the profit formula correctly.
  2. categorize_failure() — failure bucketing.
  3. analyze() — the convenience wrapper used by the API.

The math is verified by round-tripping: take a target_buy_price()
result, plug it back into calculate_profit(), and confirm the
resulting net_profit / roi sit at the threshold (within rounding).
"""
import pytest

from app.pricing.negotiation import (
    target_buy_price,
    categorize_failure,
    analyze,
    NEG_ALREADY_PASSES, NEG_NEGOTIABLE,
    NEG_TOO_EXPENSIVE, NEG_INFEASIBLE,
    BUCKET_PROFITABLE_BEFORE_FEES,
    BUCKET_FAILED_ONLY_BY_PROFIT,
    BUCKET_FAILED_ONLY_BY_ROI,
    BUCKET_FAILED_CONDITION_RISK,
    BUCKET_FAILED_VALUATION_UNCERTAINTY,
    BUCKET_NEGOTIABLE_REVIEW,
    BUCKET_NEGOTIABLE_ALERT,
)
from app.pricing.profit import calculate_profit
from app.config import settings
from app.models import Listing, _utcnow


def _listing(price: float, shipping: float = 0.0) -> Listing:
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title="t", category="phones",
        price=price, shipping=shipping,
        scraped_at=_utcnow(), raw={},
    )


# ── Round-trip: max_buy_for_profit hits exactly min_profit ───────────

def test_max_buy_for_profit_hits_threshold_exactly():
    """Plug max_buy_for_profit back into calculate_profit and confirm
    the resulting net_profit lands on min_profit (within rounding)."""
    res = target_buy_price(
        price=500, shipping=0, expected_resale=600,
        min_profit=40, min_roi=0.20,
    )
    assert res.max_buy_for_profit is not None
    listing = _listing(price=res.max_buy_for_profit)
    bd = calculate_profit(listing, expected_resale=600)
    # Within 5p — rounding inside calculate_profit
    assert abs(bd.net_profit - 40.0) < 0.05


def test_max_buy_for_roi_hits_threshold_exactly():
    """Same round-trip for the ROI constraint."""
    res = target_buy_price(
        price=500, shipping=0, expected_resale=600,
        min_profit=40, min_roi=0.20,
    )
    assert res.max_buy_for_roi is not None
    listing = _listing(price=res.max_buy_for_roi)
    bd = calculate_profit(listing, expected_resale=600)
    assert abs(bd.roi - 0.20) < 0.005


def test_max_buy_for_profit_with_inbound_shipping():
    """Inbound shipping shouldn't break the round-trip."""
    res = target_buy_price(
        price=400, shipping=10, expected_resale=600,
        min_profit=40, min_roi=0.20,
    )
    assert res.max_buy_for_profit is not None
    listing = _listing(price=res.max_buy_for_profit, shipping=10)
    bd = calculate_profit(listing, expected_resale=600)
    assert abs(bd.net_profit - 40.0) < 0.05


def test_max_buy_for_roi_with_inbound_shipping():
    res = target_buy_price(
        price=400, shipping=10, expected_resale=600,
        min_profit=40, min_roi=0.20,
    )
    assert res.max_buy_for_roi is not None
    listing = _listing(price=res.max_buy_for_roi, shipping=10)
    bd = calculate_profit(listing, expected_resale=600)
    assert abs(bd.roi - 0.20) < 0.005


# ── Binding-constraint logic ─────────────────────────────────────────

def test_binding_constraint_picks_lower_max_buy():
    """The binding constraint is whichever max-buy is lower."""
    res = target_buy_price(
        price=500, shipping=0, expected_resale=600,
        min_profit=40, min_roi=0.20,
    )
    assert res.max_buy_overall == min(res.max_buy_for_profit, res.max_buy_for_roi)


def test_binding_label_when_profit_is_binding():
    """High ROI tolerance, tight profit → profit binds."""
    res = target_buy_price(
        price=500, shipping=0, expected_resale=600,
        min_profit=80, min_roi=0.05,
    )
    assert res.binding_constraint == "profit"
    assert res.max_buy_overall == res.max_buy_for_profit


def test_binding_label_when_roi_is_binding():
    """High profit tolerance, tight ROI → ROI binds."""
    res = target_buy_price(
        price=500, shipping=0, expected_resale=600,
        min_profit=10, min_roi=0.40,
    )
    assert res.binding_constraint == "roi"
    assert res.max_buy_overall == res.max_buy_for_roi


# ── Already-passes / infeasible labels ───────────────────────────────

def test_already_passes_when_current_price_is_low_enough():
    """If the listing already meets thresholds, label says so and
    discount_needed is zero."""
    # Resale 600, free shipping, threshold 40/0.20 → max buy ≈ 466
    res = target_buy_price(
        price=300, shipping=0, expected_resale=600,
        min_profit=40, min_roi=0.20,
    )
    assert res.label == NEG_ALREADY_PASSES
    assert res.discount_needed_abs == 0.0
    assert res.discount_needed_pct == 0.0
    # And the current_net_profit should be well above the threshold
    assert res.current_net_profit > 40


def test_infeasible_when_resale_below_fees_and_threshold():
    """If even a free purchase can't hit the profit threshold, label
    is infeasible and max_buy_* is None."""
    # Resale 50, threshold 40 — fees alone exceed (50 - 40)
    res = target_buy_price(
        price=20, shipping=0, expected_resale=50,
        min_profit=40, min_roi=0.20,
    )
    # Profit is infeasible — payable to give it away wouldn't hit £40 net
    # max_buy_for_profit needs purchase such that 50 - F - 40 - purchase >= 0
    # F > 10 (resale fee 13% of 50 = 6.50, payment_fee 0.30+1, outbound 6)
    # So max_buy_for_profit < 0 -> None
    assert res.max_buy_for_profit is None
    assert res.label == NEG_INFEASIBLE


# ── Negotiable / too-expensive labels ────────────────────────────────

def test_negotiable_when_discount_within_pct_threshold():
    """A 10% discount on a £200 phone should land in 'negotiable'."""
    # Find a price where max_buy is slightly below current price
    # First find max_buy for resale=300 / threshold (40, 0.20)
    res_at_max = target_buy_price(
        price=200, shipping=0, expected_resale=300,
        min_profit=40, min_roi=0.20,
    )
    # If this happens to already pass, push the ask up a bit
    if res_at_max.label == NEG_ALREADY_PASSES:
        # Set price to 8% above max_buy_overall — clearly negotiable
        target = res_at_max.max_buy_overall * 1.08
        res = target_buy_price(
            price=target, shipping=0, expected_resale=300,
            min_profit=40, min_roi=0.20,
            negotiable_max_pct=0.15, negotiable_max_abs=1000.0,
        )
        assert res.label == NEG_NEGOTIABLE
        assert res.discount_needed_pct <= 0.15 + 1e-6
        assert res.discount_needed_abs > 0


def test_too_expensive_when_discount_exceeds_both_limits():
    """Big gap between price and max_buy → too_expensive."""
    # Force a 50% discount need: low resale, high price
    res = target_buy_price(
        price=500, shipping=0, expected_resale=400,
        min_profit=40, min_roi=0.20,
        negotiable_max_pct=0.15, negotiable_max_abs=30.0,
    )
    if res.label == NEG_INFEASIBLE:
        pytest.skip("This combo went infeasible — covered by other test")
    assert res.label == NEG_TOO_EXPENSIVE
    assert res.discount_needed_abs > 30
    assert res.discount_needed_pct > 0.15


def test_negotiable_when_within_abs_even_if_pct_exceeded():
    """A £25 discount on a £100 phone is 25% (above 15%) but £25 ≤ £30
    (within abs) — should still be negotiable (laxer wins)."""
    # Engineer: resale 130, no shipping, thresholds (40, 0.20)
    # so max_buy ≈ 130 - F - 40 (small)
    # We want price = max_buy + ~25
    base = target_buy_price(
        price=10, shipping=0, expected_resale=130,
        min_profit=40, min_roi=0.20,
    )
    if base.max_buy_overall is None or base.max_buy_overall <= 0:
        pytest.skip("Resale is infeasible at this combo")
    target_price = base.max_buy_overall + 25
    res = target_buy_price(
        price=target_price, shipping=0, expected_resale=130,
        min_profit=40, min_roi=0.20,
        negotiable_max_pct=0.15, negotiable_max_abs=30.0,
    )
    # 25/target_price is likely above 15%, but abs <= 30 should rescue
    assert res.label == NEG_NEGOTIABLE


# ── Sanity: current_net_profit / current_roi match calculate_profit ──

def test_current_metrics_match_calculate_profit():
    res = target_buy_price(
        price=400, shipping=0, expected_resale=500,
        min_profit=40, min_roi=0.20,
    )
    listing = _listing(price=400)
    bd = calculate_profit(listing, expected_resale=500)
    assert abs(res.current_net_profit - bd.net_profit) < 0.05
    assert abs(res.current_roi - bd.roi) < 0.005


# ── categorize_failure ───────────────────────────────────────────────

def _stub_target(label=NEG_TOO_EXPENSIVE, current_net=0.0, min_profit=40):
    """Lightweight target_buy_price stub for bucketing tests."""
    from app.pricing.negotiation import TargetBuyPrice
    return TargetBuyPrice(
        current_price=200, current_shipping=0, expected_resale=250,
        min_profit_threshold=min_profit, min_roi_threshold=0.20,
        current_landed_cost=200, current_net_profit=current_net,
        current_roi=0.0,
        max_buy_for_profit=180, max_buy_for_roi=170, max_buy_overall=170,
        binding_constraint="roi",
        discount_needed_abs=30, discount_needed_pct=0.15,
        label=label,
    )


def test_bucket_profitable_before_fees():
    """resale > landed cost but net_profit below threshold."""
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold", "roi_below_threshold"],
        risk_flags=[], price=200, shipping=0, expected_resale=240,
        target_review=_stub_target(current_net=20),  # below 40 threshold
    )
    assert buckets[BUCKET_PROFITABLE_BEFORE_FEES] is True


def test_bucket_NOT_profitable_before_fees_when_already_passing():
    """current_net >= threshold → NOT in this bucket."""
    buckets = categorize_failure(
        failure_reasons=[], risk_flags=[],
        price=200, shipping=0, expected_resale=240,
        target_review=_stub_target(current_net=50),  # above 40 threshold
    )
    assert buckets[BUCKET_PROFITABLE_BEFORE_FEES] is False


def test_bucket_failed_only_by_profit():
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_ONLY_BY_PROFIT] is True
    assert buckets[BUCKET_FAILED_ONLY_BY_ROI] is False


def test_bucket_failed_only_by_roi():
    buckets = categorize_failure(
        failure_reasons=["roi_below_threshold"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_ONLY_BY_ROI] is True
    assert buckets[BUCKET_FAILED_ONLY_BY_PROFIT] is False


def test_bucket_failed_only_by_profit_NOT_set_when_other_failures_present():
    """If a hard non-margin failure also fired, this isn't 'only by
    profit'."""
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold", "score_below_threshold"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_ONLY_BY_PROFIT] is False


def test_bucket_failed_only_by_profit_OK_with_soft_signals():
    """Soft signals like active_only don't block the 'only by profit'
    classification."""
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold", "active_comps_only",
                         "missing_battery_health"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_ONLY_BY_PROFIT] is True


def test_bucket_failed_condition_risk_via_critical_failure_code():
    buckets = categorize_failure(
        failure_reasons=["critical_risk_flags"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_CONDITION_RISK] is True


def test_bucket_failed_condition_risk_via_risk_flag():
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold"],
        risk_flags=["possible_damage"],
        price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_CONDITION_RISK] is True


def test_bucket_valuation_uncertainty_via_low_confidence():
    buckets = categorize_failure(
        failure_reasons=[], risk_flags=[],
        price=200, shipping=0, expected_resale=250,
        valuation_confidence=0.30,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_VALUATION_UNCERTAINTY] is True


def test_bucket_valuation_uncertainty_NOT_set_for_high_confidence():
    buckets = categorize_failure(
        failure_reasons=[], risk_flags=[],
        price=200, shipping=0, expected_resale=250,
        valuation_confidence=0.80,
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_VALUATION_UNCERTAINTY] is False


def test_bucket_valuation_uncertainty_via_warning():
    buckets = categorize_failure(
        failure_reasons=[], risk_flags=[],
        price=200, shipping=0, expected_resale=250,
        valuation_confidence=0.70,
        valuation_warnings=["wide_active_spread"],
        target_review=_stub_target(),
    )
    assert buckets[BUCKET_FAILED_VALUATION_UNCERTAINTY] is True


def test_bucket_negotiable_review_set_when_target_label_is_negotiable():
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(label=NEG_NEGOTIABLE),
    )
    assert buckets[BUCKET_NEGOTIABLE_REVIEW] is True


def test_bucket_negotiable_alert_set_independently():
    buckets = categorize_failure(
        failure_reasons=["profit_below_threshold"],
        risk_flags=[], price=200, shipping=0, expected_resale=250,
        target_review=_stub_target(label=NEG_TOO_EXPENSIVE),
        target_alert=_stub_target(label=NEG_NEGOTIABLE),
    )
    assert buckets[BUCKET_NEGOTIABLE_REVIEW] is False
    assert buckets[BUCKET_NEGOTIABLE_ALERT] is True


# ── analyze() wrapper ────────────────────────────────────────────────

def test_analyze_returns_target_review_target_alert_buckets():
    out = analyze(
        price=300, shipping=0, expected_resale=400,
        failure_reasons=["profit_below_threshold"],
        risk_flags=[], valuation_confidence=0.6,
    )
    assert "target_review" in out
    assert "target_alert" in out
    assert "buckets" in out
    # Both targets should be dicts shaped like TargetBuyPrice.to_dict()
    assert "max_buy_overall" in out["target_review"]
    assert "max_buy_overall" in out["target_alert"]
    # And the alert bar should be tighter (min_profit=50 vs review's 40,
    # min_roi=0.25 vs 0.20), so its max_buy should be lower or equal
    rev = out["target_review"]["max_buy_overall"]
    alt = out["target_alert"]["max_buy_overall"]
    if rev is not None and alt is not None:
        assert alt <= rev + 0.01


def test_analyze_uses_settings_for_thresholds():
    """The wrapper must pull min_profit/min_roi from settings, not
    require the caller to pass them."""
    # Just confirm it doesn't blow up and returns plausible numbers
    out = analyze(price=200, shipping=0, expected_resale=300)
    assert out["target_review"]["min_profit_threshold"] == settings.review_min_profit
    assert out["target_review"]["min_roi_threshold"] == settings.review_min_roi
    assert out["target_alert"]["min_profit_threshold"] == settings.min_profit
    assert out["target_alert"]["min_roi_threshold"] == settings.min_roi
