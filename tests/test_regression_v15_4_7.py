"""
Regression tests for v15.4.7 — relative condition discount and
multi-variant comp filtering.

Driven by the Apr 27 14:30 DB dump showing iPhone 14 Pro Max 128GB
estimated at £291.71 when comp median was £390.99 (used/good condition).
The bot was applying a 0.85 "good condition" multiplier on top of the
0.88 active-listing discount on top of comps that were ALREADY good
condition — double discounting.
"""
import pytest
import statistics
from app.pricing.comps import (
    _detect_comp_condition, _avg_comp_condition_value,
    _is_multi_variant, _build_match_from_set, CompEntry,
    _tier_for_phone, TIER_EXACT, TIER_PARTIAL, TIER_BROAD,
)
from app.pricing.ebay_comps import _is_multi_variant_listing
from app.models import NormalizedIdentity


# ── Multi-variant detection ────────────────────────────────────────

class TestMultiVariantDetection:
    def test_all_colours_is_multi_variant(self):
        assert _is_multi_variant(
            "Apple iPhone 14 Pro Max 128GB - ALL COLOURS - UNLOCKED"
        ) is True

    def test_all_sizes_is_multi_variant(self):
        assert _is_multi_variant(
            "Apple iPhone 15 Pro - All Sizes - Unlocked - All Colors"
        ) is True

    def test_choose_colour_is_multi_variant(self):
        assert _is_multi_variant(
            "Apple iPhone 14 Pro 256GB Choose Colour"
        ) is True

    def test_multi_storage_slash_format(self):
        """The 128/256/512GB pattern from Adam's data."""
        assert _is_multi_variant(
            "Apple iPhone 15 Pro 128GB/256/512 - ALL COLOURS - UNLOCKED"
        ) is True

    def test_multi_storage_with_gb_each(self):
        assert _is_multi_variant(
            "Apple iPhone 15 Pro 128GB/256GB/512GB UNLOCKED"
        ) is True

    def test_from_price_is_multi_variant(self):
        assert _is_multi_variant(
            "Apple iPhone 14 Pro from £299"
        ) is True

    def test_single_variant_listing_is_not_multi(self):
        assert _is_multi_variant(
            "Apple iPhone 14 Pro Max 128GB Deep Purple Unlocked"
        ) is False

    def test_single_variant_with_only_one_storage(self):
        assert _is_multi_variant(
            "Apple iPhone 13 Pro 256GB Sierra Blue"
        ) is False


# ── Condition extraction ───────────────────────────────────────────

class TestConditionExtraction:
    def test_for_parts_extracted_as_parts(self):
        assert _detect_comp_condition(
            "iPhone 14 Pro for parts only"
        ) == "parts"

    def test_very_good_extracted_as_like_new(self):
        assert _detect_comp_condition(
            "Apple iPhone 14 Pro Max 128GB Very Good Condition"
        ) == "like_new"

    def test_excellent_extracted_as_like_new(self):
        assert _detect_comp_condition(
            "iPhone 15 Pro Excellent Condition"
        ) == "like_new"

    def test_brand_new_extracted_as_new(self):
        assert _detect_comp_condition(
            "Apple iPhone 15 Pro 128GB Brand New Sealed"
        ) == "new"

    def test_good_extracted_as_good(self):
        assert _detect_comp_condition(
            "Apple iPhone 14 Pro Max 128GB Good Condition Unlocked"
        ) == "good"

    def test_no_condition_info_returns_unknown(self):
        assert _detect_comp_condition(
            "Apple iPhone 14 Pro Max 128GB Unlocked"
        ) == "unknown"


# ── Pool average condition ─────────────────────────────────────────

class TestPoolAverage:
    def test_pool_of_good_listings_avg_85(self):
        titles = [
            "iPhone 14 Pro Max - Good Condition",
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max - Good Condition Unlocked",
        ]
        avg, breakdown = _avg_comp_condition_value(titles)
        assert avg == pytest.approx(0.85, abs=0.01)

    def test_pool_with_excellent_pulls_avg_up(self):
        titles = [
            "iPhone 14 Pro Max - Good Condition",
            "iPhone 14 Pro Max - Very Good",
            "iPhone 14 Pro Max - Excellent",
        ]
        avg, _ = _avg_comp_condition_value(titles)
        assert avg > 0.85

    def test_unknown_titles_default_to_good_baseline(self):
        titles = ["iPhone 14 Pro 128GB", "iPhone 14 Pro 128GB"]
        avg, breakdown = _avg_comp_condition_value(titles)
        # All unknown → fallback to "good" baseline
        assert avg == pytest.approx(0.85, abs=0.01)
        assert breakdown["unknown"] == 2


# ── End-to-end relative discount ───────────────────────────────────

class TestRelativeDiscount:
    """The core fix: target=good vs comp_pool=good should NOT double-discount."""

    def _make_match(self, prices, titles, target_condition="good"):
        """Helper to invoke _build_match_from_set."""
        identity = NormalizedIdentity(
            brand="Apple", model="iphone 14 pro max", category="phones",
            storage_gb=128, carrier="unlocked", condition=target_condition,
        )
        entry = CompEntry(prices=prices, source="active",
                          spec={"storage_gb": 128, "carrier": "unlocked"},
                          titles=titles)
        return _build_match_from_set(
            identity, entry, prices, titles,
            match_quality=0.85, tier_label="exact",
        )

    def test_good_target_against_good_pool_does_not_double_discount(self):
        """The Adam bug: median £390.99 should NOT compress to £291."""
        prices = [377.95, 390.99, 390.99, 411.99]
        titles = [
            "Apple iPhone 14 Pro Max 128GB Very Good Condition",
            "Apple iPhone 14 Pro Max 128GB Good Condition",
            "Apple iPhone 14 Pro Max 128GB Good Condition",
            "Apple iPhone 14 Pro Max with New Battery 128GB Good",
        ]
        result = self._make_match(prices, titles, target_condition="good")

        # Old math would have produced ~£292 (390.99 * 0.88 * 0.85)
        # New math: 390.99 * 0.88 * (0.85 / pool_avg)
        # pool avg with one like_new (very good) and three good ≈ 0.87
        # so cond_adjustment ≈ 0.97 → final ≈ £334
        assert result.expected_resale > 320, (
            f"Expected resale should not be over-discounted; got "
            f"£{result.expected_resale}"
        )
        # And we shouldn't inflate above the pool either
        assert result.expected_resale < statistics.median(prices) * 0.95

    def test_target_worse_than_pool_does_get_extra_discount(self):
        """If target is fair but pool is good, target SHOULD be discounted."""
        prices = [400, 400, 400]
        titles = [
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
        ]
        good_target = self._make_match(prices, titles, target_condition="good")
        fair_target = self._make_match(prices, titles, target_condition="fair")
        # Fair target should get a meaningful additional discount
        assert fair_target.expected_resale < good_target.expected_resale
        ratio = fair_target.expected_resale / good_target.expected_resale
        # Fair vs good ratio is roughly 0.72 / 0.85 ≈ 0.85
        assert 0.80 < ratio < 0.90

    def test_target_better_than_pool_does_not_inflate(self):
        """If target is excellent but pool is good, we don't inflate above pool."""
        prices = [400, 400, 400]
        titles = [
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
        ]
        good_target = self._make_match(prices, titles, target_condition="good")
        new_target = self._make_match(prices, titles, target_condition="new")
        # cond_adjustment is capped at 1.0 — we never inflate above pool
        # So new_target should equal good_target (both apply the active
        # discount but no condition adjustment)
        assert new_target.expected_resale == good_target.expected_resale

    def test_breakdown_is_in_match_details(self):
        """The dashboard needs to see the math."""
        prices = [400, 400, 400, 400]
        titles = [
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
            "iPhone 14 Pro Max Good Condition",
        ]
        result = self._make_match(prices, titles, target_condition="good")
        # match_details should mention raw_median, active, cond
        assert "raw_median" in result.match_details
        assert "active" in result.match_details
        assert "cond" in result.match_details


# ── Multi-variant downgrades to PARTIAL ────────────────────────────

class TestMultiVariantDowngrade:
    def test_multi_variant_comp_is_partial_not_exact(self):
        """An "All Colours" comp can't be EXACT even with matching specs."""
        identity = NormalizedIdentity(
            brand="Apple", model="iphone 14 pro max", category="phones",
            storage_gb=128, carrier="unlocked", condition="good",
        )
        comp_spec = {"storage_gb": 128, "carrier": "unlocked"}
        comp_title = "Apple iPhone 14 Pro Max 128GB - ALL COLOURS - UNLOCKED"
        comp_model = "iphone 14 pro max"
        tier, reason = _tier_for_phone(
            comp_spec, identity, comp_title, comp_model,
        )
        assert tier == TIER_PARTIAL
        assert "multi-variant" in reason.lower()

    def test_single_variant_with_matching_specs_is_exact(self):
        """Sanity check: a normal single-variant comp still gets EXACT."""
        identity = NormalizedIdentity(
            brand="Apple", model="iphone 14 pro max", category="phones",
            storage_gb=128, carrier="unlocked", condition="good",
        )
        comp_spec = {"storage_gb": 128, "carrier": "unlocked"}
        comp_title = "Apple iPhone 14 Pro Max - 128GB - Deep Purple (Unlocked)"
        tier, reason = _tier_for_phone(
            comp_spec, identity, comp_title, "iphone 14 pro max",
        )
        assert tier == TIER_EXACT


# ── Multi-variant fetch filtering ──────────────────────────────────

class TestMultiVariantFetchFilter:
    def test_multi_variant_listing_helper_works(self):
        """The fetcher's _is_multi_variant_listing wraps comps._is_multi_variant."""
        assert _is_multi_variant_listing(
            "Apple iPhone 14 Pro Max 128GB/256/512 - ALL COLOURS - UNLOCKED"
        ) is True
        assert _is_multi_variant_listing(
            "Apple iPhone 14 Pro Max 128GB Deep Purple Unlocked"
        ) is False
