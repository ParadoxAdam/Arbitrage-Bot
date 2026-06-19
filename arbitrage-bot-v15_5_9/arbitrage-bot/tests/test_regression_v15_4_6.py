"""
Regression tests for v15.4.6 — versioning, category toggle, harmonized risk.
"""
import pytest
from app.config import (
    APP_VERSION, VALUATION_VERSION, CURRENT_ENGINE_VERSION, settings,
)
from app.models import Listing, NormalizedIdentity, _utcnow
from app.scoring import detect_risk_flags, CRITICAL_FLAGS


def _phone_listing(title, condition="good"):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category="phones",
        price=400, shipping=0, condition=condition,
        scraped_at=_utcnow(), raw={},
    )


def _identity_phone():
    return NormalizedIdentity(
        brand="Apple", model="iphone 14 pro", category="phones",
        storage_gb=256, condition="good",
    )


# ── Versioning ──────────────────────────────────────────────────────

def test_versions_are_v15_4_6():
    assert APP_VERSION.startswith("v15.")
    assert VALUATION_VERSION.startswith("v15.")
    # NOTE: prior to v15.5.9 these were strictly equal. v15.5.9 was
    # a UI-only / analytics-only release (no scoring or comping logic
    # changed), so VALUATION_VERSION deliberately did not advance —
    # this lets analytics keep grouping rows from before and after the
    # release. APP_VERSION is allowed to be ahead of VALUATION_VERSION
    # but never behind it.
    assert APP_VERSION >= VALUATION_VERSION


def test_current_engine_version_aliases_valuation_version():
    """Backwards-compat alias kept in v15.4.6."""
    assert CURRENT_ENGINE_VERSION == VALUATION_VERSION


# ── Category toggle ─────────────────────────────────────────────────

def test_default_categories_enabled_is_phones_only(monkeypatch):
    """v15.4.6 default: phones-only validation phase."""
    monkeypatch.setattr(settings, "categories_enabled", "phones")
    assert settings.enabled_categories == {"phones"}


def test_categories_enabled_parses_csv(monkeypatch):
    monkeypatch.setattr(settings, "categories_enabled", "phones,shoes")
    assert settings.enabled_categories == {"phones", "shoes"}


def test_categories_enabled_handles_whitespace(monkeypatch):
    monkeypatch.setattr(settings, "categories_enabled", " phones , shoes ")
    assert settings.enabled_categories == {"phones", "shoes"}


def test_get_queries_respects_phones_only(monkeypatch):
    """With CATEGORIES_ENABLED=phones, no shoe/laptop queries should run."""
    monkeypatch.setattr(settings, "categories_enabled", "phones")
    from app.queries import get_queries
    queries = get_queries()
    assert len(queries) > 0
    assert all(q.category == "phones" for q in queries)


def test_get_queries_with_all_categories(monkeypatch):
    monkeypatch.setattr(settings, "categories_enabled", "phones,shoes,laptops")
    from app.queries import get_queries
    queries = get_queries()
    cats = {q.category for q in queries}
    assert "phones" in cats
    assert "shoes" in cats
    assert "laptops" in cats


# ── Harmonized risk detection ───────────────────────────────────────

class TestSoftDamageFlag:
    """Soft damage signals flag the listing but aren't critical kills."""

    def test_cosmetic_damage_flagged_as_possible_damage(self):
        l = _phone_listing(
            "Apple iPhone 14 Pro 256GB Cosmetic Damage but works"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "possible_damage" in flags
        # Should NOT be critical — listing might still be valid
        assert "possible_damage" not in CRITICAL_FLAGS

    def test_read_description_flagged(self):
        l = _phone_listing(
            "Apple iPhone 14 Pro 256GB Unlocked Read Description"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "possible_damage" in flags

    def test_reparable_flagged(self):
        l = _phone_listing(
            "Apple iPhone 14 Pro 256GB Reparable Battery Issue"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "possible_damage" in flags

    def test_clean_listing_no_soft_flag(self):
        l = _phone_listing(
            "Apple iPhone 14 Pro 256GB Excellent Condition Unlocked"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "possible_damage" not in flags


class TestStrongDamageFlag:
    """Strong damage signals stay critical — they kill the score."""

    def test_for_parts_is_critical(self):
        l = _phone_listing(
            "Apple iPhone 14 Pro 256GB FOR PARTS Cracked Screen"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "damaged_or_parts" in flags
        assert "damaged_or_parts" in CRITICAL_FLAGS

    def test_spares_or_repair_is_critical(self):
        l = _phone_listing(
            "iPhone 14 Pro 256GB Spares or Repair"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "damaged_or_parts" in flags

    def test_broken_no_space_before(self):
        """The (Unlocked)Broken Back bug from comp filter — also fixed
        for main listing risk detection."""
        l = _phone_listing(
            "iPhone 14 Pro 256GB (Unlocked)Broken Back"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "damaged_or_parts" in flags

    def test_icloud_locked_is_critical(self):
        l = _phone_listing(
            "iPhone 14 Pro 256GB iCloud Locked"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "damaged_or_parts" in flags or "locked_phone" in flags


class TestWordBoundaryRiskMatching:
    """Risk keywords use word boundaries — no false positives on substrings."""

    def test_casey_does_not_match_case(self):
        l = _phone_listing(
            "iPhone 14 Pro Owned by Casey 256GB Unlocked"
        )
        flags = detect_risk_flags(l, _identity_phone())
        assert "accessory_not_product" not in flags

    def test_repaired_does_not_match_repair(self):
        """v15.4.6 word boundary fix: 'repaired' should NOT trigger 'for repair'.
        Multi-token like 'for repair' uses substring match, so we test the
        single-token DAMAGE keywords here."""
        l = _phone_listing(
            "iPhone 14 Pro Professionally Repaired by Apple Store"
        )
        # 'broken' shouldn't fire either
        flags = detect_risk_flags(l, _identity_phone())
        # We expect NO damage flag because "Repaired" doesn't equal "for repair"
        # and there's no "broken" / "cracked" etc.
        assert "damaged_or_parts" not in flags
