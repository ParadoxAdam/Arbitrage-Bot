"""
Regression tests for v15.4.8 — multi-variant candidates and suspicious
new claims.

Driven by Apr 28 DB dump showing:
- "iPhone 14 Pro 128-512gb" candidate getting valued (storage normalizer
  picked the 512gb variant but the listing is multi-storage)
- "NEW BOXED Apple Replacement UK" listings getting analysed as if they
  were genuine new phones
"""
import pytest
from app.scoring import detect_risk_flags, CRITICAL_FLAGS
from app.models import Listing, NormalizedIdentity, _utcnow


def _phone(title, condition="good", price=400):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category="phones",
        price=price, shipping=0, condition=condition,
        scraped_at=_utcnow(), raw={},
    )


def _identity():
    return NormalizedIdentity(
        brand="Apple", model="iphone 14 pro", category="phones",
        storage_gb=128, condition="good",
    )


# ── Multi-variant candidate detection ───────────────────────────────

class TestMultiVariantCandidate:
    def test_multi_storage_listing_gets_critical_flag(self):
        """The 'iPhone 14 Pro 128-512gb' bug: candidate is multi-storage."""
        l = _phone("Apple iPhone 14 Pro 128-512gb Unlocked very good condition")
        flags = detect_risk_flags(l, _identity())
        assert "multi_variant_candidate" in flags
        assert "multi_variant_candidate" in CRITICAL_FLAGS

    def test_all_colours_listing_gets_critical_flag(self):
        l = _phone("Apple iPhone 14 Pro 128GB - ALL COLOURS - UNLOCKED")
        flags = detect_risk_flags(l, _identity())
        assert "multi_variant_candidate" in flags

    def test_all_sizes_listing_gets_critical_flag(self):
        l = _phone("Apple iPhone 15 Pro - All Sizes - All Colours - Unlocked")
        flags = detect_risk_flags(l, _identity())
        assert "multi_variant_candidate" in flags

    def test_choose_storage_listing_gets_critical_flag(self):
        l = _phone("iPhone 14 Pro Unlocked Choose Storage")
        flags = detect_risk_flags(l, _identity())
        assert "multi_variant_candidate" in flags

    def test_single_variant_listing_no_flag(self):
        """Sanity: a normal single-variant listing doesn't get the flag."""
        l = _phone("Apple iPhone 14 Pro 128GB Deep Purple Unlocked")
        flags = detect_risk_flags(l, _identity())
        assert "multi_variant_candidate" not in flags


# ── Suspicious new claim detection ──────────────────────────────────

class TestSuspiciousNewClaim:
    def test_apple_replacement_uk_flagged(self):
        """The 'NEW BOXED Apple Replacement UK' bug — these are
        third-party refurbs sold as new at inflated prices."""
        l = _phone(
            "NEW BOXED Apple iPhone 13 Pro 256GB Blue 5G Unlocked - Apple Replacement UK",
            condition="new", price=649,
        )
        flags = detect_risk_flags(l, _identity())
        assert "suspicious_new_claim" in flags
        assert "suspicious_new_claim" in CRITICAL_FLAGS

    def test_aftermarket_flagged(self):
        l = _phone("iPhone 14 Pro 128GB Aftermarket Housing Unlocked")
        flags = detect_risk_flags(l, _identity())
        assert "suspicious_new_claim" in flags

    def test_non_genuine_flagged(self):
        l = _phone("iPhone 14 Pro 128GB Unlocked Non Genuine Screen")
        flags = detect_risk_flags(l, _identity())
        assert "suspicious_new_claim" in flags

    def test_replacement_screen_flagged(self):
        l = _phone("iPhone 13 Pro 128GB Replacement Screen Unlocked")
        flags = detect_risk_flags(l, _identity())
        assert "suspicious_new_claim" in flags

    def test_genuine_new_listing_not_flagged(self):
        """A genuinely new listing shouldn't trigger this."""
        l = _phone(
            "Apple iPhone 14 Pro 128GB Brand New Sealed Apple Store",
            condition="new", price=900,
        )
        flags = detect_risk_flags(l, _identity())
        assert "suspicious_new_claim" not in flags


# ── Pre-comp skip behaviour ─────────────────────────────────────────

class TestPreCompSkip:
    """Listings with these critical flags should be skipped before comps
    are fetched (save API quota)."""

    def test_critical_flag_set_completeness(self):
        """All v15.4.8 flags are in CRITICAL_FLAGS."""
        assert "multi_variant_candidate" in CRITICAL_FLAGS
        assert "suspicious_new_claim" in CRITICAL_FLAGS
        # And we kept all the existing ones
        assert "damaged_or_parts" in CRITICAL_FLAGS
        assert "accessory_not_product" in CRITICAL_FLAGS
        assert "below_price_floor" in CRITICAL_FLAGS
        assert "locked_phone" in CRITICAL_FLAGS
