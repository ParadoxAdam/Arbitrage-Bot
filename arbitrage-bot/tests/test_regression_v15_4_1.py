"""
Regression tests for v15.4.1 — near-miss bug fix.

The previous version marked every scored listing that failed review as
is_near_miss=True, which made the recheck policy rescore every failed
listing repeatedly. This wasted API quota and undermined dedupe.

The new _is_genuine_near_miss() classifier requires that a listing be
ACTUALLY close to passing review — not just any failed listing.
"""
from types import SimpleNamespace
import pytest
from app.pipeline import _is_genuine_near_miss
from app.config import settings


def _op(*, profit=100, roi=0.5, score=0.5, confidence=0.5,
        match_quality=0.85, comp_source="active", risk_flags=()):
    return SimpleNamespace(
        net_profit=profit, roi=roi, score=score, confidence=confidence,
        match_quality=match_quality, comp_source=comp_source,
        risk_flags=list(risk_flags),
    )


# ── True positives — these ARE close, should be near-misses ────────

def test_close_to_threshold_is_near_miss():
    """Slightly below threshold on every metric — clearly close."""
    op = _op(
        profit=settings.review_min_profit * 0.9,        # 10% below
        roi=settings.review_min_roi * 0.9,
        score=settings.review_min_score * 0.9,
        confidence=settings.review_min_confidence * 0.9,
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is True


def test_just_below_profit_threshold_is_near_miss():
    """Profit just under £40 with everything else fine — close."""
    op = _op(
        profit=settings.review_min_profit * 0.85,
        score=settings.review_min_score,
        confidence=settings.review_min_confidence,
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is True


# ── False positives — these are NOT close, should NOT be near-misses ─

def test_critical_risk_flag_is_not_near_miss():
    """Even with great numbers, critical flags kill near-miss status."""
    op = _op(
        profit=settings.review_min_profit * 1.5,       # comfortably above
        roi=settings.review_min_roi * 1.5,
        score=settings.review_min_score * 1.5,
        confidence=settings.review_min_confidence * 1.5,
        match_quality=0.95,
        risk_flags=["accessory_not_product"],
    )
    assert _is_genuine_near_miss(op) is False


def test_weak_match_quality_is_not_near_miss():
    """Weak comps mean we don't trust the score — not a near-miss."""
    op = _op(
        profit=settings.review_min_profit,
        score=settings.review_min_score,
        confidence=settings.review_min_confidence,
        match_quality=0.30,    # below 0.5
    )
    assert _is_genuine_near_miss(op) is False


def test_negative_profit_is_not_near_miss():
    """Negative profit listings should not be re-evaluated as near-misses."""
    op = _op(
        profit=-100,
        roi=-1.0,
        score=settings.review_min_score,
        confidence=settings.review_min_confidence,
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is False


def test_far_below_score_threshold_is_not_near_miss():
    """A score way below threshold — not close."""
    op = _op(
        profit=settings.review_min_profit,
        score=settings.review_min_score * 0.4,    # 60% below
        confidence=settings.review_min_confidence,
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is False


def test_far_below_confidence_is_not_near_miss():
    op = _op(
        profit=settings.review_min_profit,
        score=settings.review_min_score,
        confidence=settings.review_min_confidence * 0.5,    # half threshold
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is False


def test_low_roi_is_not_near_miss_v15_4_2():
    """v15.4.2: cash profit alone isn't enough — ROI must also be close.
    A £35 profit on a £500 item has decent cash but bad margin."""
    op = _op(
        profit=settings.review_min_profit,           # passes profit
        roi=settings.review_min_roi * 0.3,           # ROI way too low
        score=settings.review_min_score,
        confidence=settings.review_min_confidence,
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is False


def test_close_to_roi_threshold_passes_v15_4_2():
    """ROI just below threshold (within 80%) should still count as near-miss."""
    op = _op(
        profit=settings.review_min_profit,
        roi=settings.review_min_roi * 0.85,          # close to threshold
        score=settings.review_min_score,
        confidence=settings.review_min_confidence,
        match_quality=0.85,
    )
    assert _is_genuine_near_miss(op) is True


# ── The actual scenario from production ────────────────────────────

def test_majority_of_failures_are_NOT_near_misses_in_realistic_distribution():
    """
    Simulate a typical scan of phone listings: most are far below threshold
    on profit, a few are close. Only the close ones should be near-misses.
    """
    realistic_failures = [
        # Most listings — overpriced, way below thresholds
        _op(profit=-200, roi=-0.5, score=0.27, confidence=0.4, match_quality=0.85),
        _op(profit=-150, roi=-0.4, score=0.28, confidence=0.5, match_quality=0.85),
        _op(profit=-50, roi=-0.1, score=0.3, confidence=0.5, match_quality=0.85),
        _op(profit=10, roi=0.05, score=0.32, confidence=0.5, match_quality=0.85),
        # A few genuinely close ones
        _op(profit=settings.review_min_profit * 0.85,
            roi=settings.review_min_roi * 0.85,
            score=settings.review_min_score * 0.9,
            confidence=settings.review_min_confidence * 0.9,
            match_quality=0.85),
    ]

    near_misses = [op for op in realistic_failures if _is_genuine_near_miss(op)]

    # Only 1 of the 5 should be a near-miss — definitely not all of them
    assert len(near_misses) == 1, (
        f"Expected exactly 1 near-miss, got {len(near_misses)}. "
        "If this test fails, the recheck policy will re-scan every failed "
        "listing, undoing dedupe."
    )
