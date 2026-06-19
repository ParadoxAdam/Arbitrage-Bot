"""
Regression tests for v15.4.5 — driven by Adam's Apr 27 13:05 DB dump
showing iPhone 15 Pro Max valuations contaminated by iPhone 15 Pro comps.

Bugs fixed:
  1. Phone normalizer regex alternation `pro|pro max` matched "pro" first
     leaving Pro Max listings tagged as "iphone 15 pro" (wrong family).
  2. No flag for explicitly-stated low battery health on the main listing.
"""
import pytest
from app.normalize import normalize
from app.models import Listing, _utcnow, NormalizedIdentity
from app.scoring import detect_risk_flags


def _listing(title, category="phones", condition="good", price=500):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category=category,
        price=price, shipping=0, condition=condition,
        scraped_at=_utcnow(), raw={},
    )


# ── Bug 1: Pro vs Pro Max regex ─────────────────────────────────────

class TestProMaxRegex:
    """Pro and Pro Max must normalize to different model strings."""

    def test_iphone_15_pro_max_keeps_max(self):
        l = _listing("Apple iPhone 15 Pro Max 256gb White Unlocked")
        identity = normalize(l)
        assert identity.model == "iphone 15 pro max"

    def test_iphone_15_pro_does_not_get_max(self):
        l = _listing("Apple iPhone 15 Pro - 256GB - Black Titanium (Unlocked)")
        identity = normalize(l)
        assert identity.model == "iphone 15 pro"

    def test_iphone_14_pro_max_keeps_max(self):
        l = _listing("Apple iPhone 14 Pro Max 256GB Deep Purple")
        identity = normalize(l)
        assert identity.model == "iphone 14 pro max"

    def test_iphone_13_pro_max_keeps_max(self):
        l = _listing("Apple iPhone 13 Pro Max 128GB Silver")
        identity = normalize(l)
        assert identity.model == "iphone 13 pro max"

    def test_pro_and_pro_max_have_different_comp_keys(self):
        pro = normalize(_listing("Apple iPhone 15 Pro 256GB Unlocked"))
        pro_max = normalize(_listing("Apple iPhone 15 Pro Max 256GB Unlocked"))
        assert pro.comp_key != pro_max.comp_key
        assert "pro max" in pro_max.comp_key
        assert "pro max" not in pro.comp_key
        # Sanity: "pro" appears in both, but only as full word vs prefix
        assert "pro" in pro.comp_key

    def test_iphone_15_pro_max_with_messy_title(self):
        """Real-world title with battery health, condition notes, etc."""
        l = _listing(
            "Apple iPhone 15 Pro Max 256gb White Unlocked 87% Battery Health"
        )
        identity = normalize(l)
        assert identity.model == "iphone 15 pro max"
        assert identity.storage_gb == 256


# ── Bug 2: Low battery health flag ──────────────────────────────────

class TestLowBatteryHealthFlag:
    def test_87_percent_battery_is_flagged(self):
        l = _listing(
            "Apple iPhone 15 Pro Max 256gb 87% Battery Health Unlocked"
        )
        identity = normalize(l)
        flags = detect_risk_flags(l, identity)
        assert "low_battery_health" in flags

    def test_85_percent_bh_is_flagged(self):
        l = _listing(
            "Apple iPhone 14 Pro 256GB Unlocked 85% BH Excellent"
        )
        identity = normalize(l)
        flags = detect_risk_flags(l, identity)
        assert "low_battery_health" in flags

    def test_100_percent_battery_is_not_flagged(self):
        l = _listing(
            "Apple iPhone 15 Pro 256GB Unlocked 100% Battery Health"
        )
        identity = normalize(l)
        flags = detect_risk_flags(l, identity)
        assert "low_battery_health" not in flags

    def test_92_percent_battery_is_not_flagged(self):
        """92% is on the edge but not flagged — Apple's healthy threshold."""
        l = _listing(
            "Apple iPhone 14 Pro 256GB Unlocked 92% Battery Health"
        )
        identity = normalize(l)
        flags = detect_risk_flags(l, identity)
        assert "low_battery_health" not in flags

    def test_no_battery_info_doesnt_set_low_flag(self):
        """No battery info → missing_battery_health, but NOT low_battery_health."""
        l = _listing(
            "Apple iPhone 14 Pro 256GB Unlocked Excellent Condition"
        )
        identity = normalize(l)
        flags = detect_risk_flags(l, identity)
        assert "missing_battery_health" in flags
        assert "low_battery_health" not in flags
