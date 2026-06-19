"""
v15.5.7 — Anchor recalibration + range-ordering invariant.

The v15.5 anchors were 10-39% too high vs actual UK eBay active medians,
which caused every iPhone Pro/Pro Max valuation to be tagged
anchor_driven_review_only.

Fixes:
1. Anchors recalibrated against Apr 28 sample data
2. New invariant: conservative ≤ expected ≤ optimistic always holds,
   even when anchor clamps would otherwise invert the range
"""
import pytest
from app.valuation import value_listing, find_anchor
from app.valuation.engine import (
    METHOD_ACTIVE_PLUS_REFERENCE, METHOD_ANCHOR_DRIVEN_REVIEW_ONLY,
)
from app.models import Listing, NormalizedIdentity, CompMatch, _utcnow


def _listing(title, price=400):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category="phones",
        price=price, shipping=0, condition="good",
        scraped_at=_utcnow(), raw={},
    )


def _identity(model="iphone 14 pro", storage_gb=128):
    return NormalizedIdentity(
        brand="Apple", model=model, category="phones",
        storage_gb=storage_gb, condition="good", carrier="unlocked",
    )


def _comp_match(expected_resale=440, prices=None):
    if prices is None:
        prices = [420, 430, 440, 445, 450]
    titles = [f"iPhone 14 Pro 128GB Unlocked Good #{i}" for i in range(len(prices))]
    return CompMatch(
        fair_value=expected_resale * 0.74,
        expected_resale=expected_resale,
        confidence=0.55, sample_size=len(prices),
        liquidity=0.5, source="active", match_quality=0.85,
        match_details="exact",
        comp_evidence=[{"price": p, "title": t}
                       for p, t in zip(prices, titles)],
    )


# ── Recalibration: anchors closer to actual market ─────────────────

class TestRecalibratedAnchors:
    def test_iphone_15_pro_max_anchor_is_realistic(self):
        """v15.5.7: anchor should match Apr 28 observed median £496.30, not £820."""
        a = find_anchor("phones", "Apple", "iphone 15 pro max", 256, "unlocked")
        assert a is not None
        assert 450 <= a.mid <= 550, (
            f"iPhone 15 Pro Max anchor mid {a.mid} should sit ~£500 "
            f"(actual UK active median Apr 28 was £496)"
        )

    def test_iphone_14_pro_max_anchor_is_realistic(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro max", 128, "unlocked")
        assert a is not None
        assert 350 <= a.mid <= 430, (
            f"iPhone 14 Pro Max 128 anchor mid {a.mid} should sit ~£390"
        )

    def test_iphone_13_pro_max_anchor_is_realistic(self):
        a = find_anchor("phones", "Apple", "iphone 13 pro max", 256, "unlocked")
        assert a is not None
        assert 320 <= a.mid <= 380


class TestNoMoreAnchorDrivenForHealthyMarket:
    """With recalibrated anchors, normal-priced iPhone listings shouldn't
    trigger anchor_driven_review_only just because of stale anchor data."""

    def test_iphone_15_pro_max_at_market_price_not_anchor_driven(self):
        """Comp median £496 + anchor mid £496 → no disagreement → not anchor-driven."""
        l = _listing("Apple iPhone 15 Pro Max 256GB Unlocked")
        i = _identity(model="iphone 15 pro max", storage_gb=256)
        # Comps showing the actual market: median ~£496
        cm = _comp_match(
            expected_resale=440,    # post-discount
            prices=[465, 470, 496, 499, 499],
        )
        v = value_listing(l, i, cm)
        assert v.valuation_method != METHOD_ANCHOR_DRIVEN_REVIEW_ONLY, (
            f"got method={v.valuation_method} with warnings={v.warnings}"
        )
        assert "valuation_suspicious_low" not in v.warnings


# ── Range invariant ─────────────────────────────────────────────────

class TestRangeInvariant:
    """conservative ≤ expected ≤ optimistic must always hold."""

    def test_invariant_holds_for_normal_case(self):
        l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
        i = _identity()
        cm = _comp_match(expected_resale=350)    # below anchor mid
        v = value_listing(l, i, cm)
        assert v.conservative_resale <= v.expected_resale
        assert v.expected_resale <= v.optimistic_resale

    def test_invariant_holds_when_v2_above_anchor_high(self):
        """The original failure case: v2 expected exceeds anchor.high × 1.05."""
        l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
        i = _identity()
        # Comp data well above the anchor
        cm = _comp_match(
            expected_resale=440,
            prices=[420, 430, 440, 445, 450],
        )
        v = value_listing(l, i, cm)
        assert v.conservative_resale <= v.expected_resale
        assert v.expected_resale <= v.optimistic_resale

    def test_invariant_holds_when_v2_below_anchor_low(self):
        """v2 expected far below anchor.low × 0.95 — invariant still holds."""
        l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
        i = _identity()
        anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
        # Force estimate well below anchor (e.g. lots of damaged comps)
        cm = _comp_match(
            expected_resale=anchor.low * 0.4,
            prices=[100, 110, 120, 130, 140],
        )
        v = value_listing(l, i, cm)
        assert v.conservative_resale <= v.expected_resale
        assert v.expected_resale <= v.optimistic_resale

    def test_invariant_holds_with_no_anchor(self):
        """Models without anchors still get a sensible range."""
        l = _listing("Some Unknown Phone")
        i = _identity(model="unknown phone")
        cm = _comp_match(expected_resale=200)
        v = value_listing(l, i, cm)
        assert v.conservative_resale <= v.expected_resale
        assert v.expected_resale <= v.optimistic_resale
