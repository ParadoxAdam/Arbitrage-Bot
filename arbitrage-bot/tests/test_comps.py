"""Tests for the tiered comp engine."""
import pytest
from app.pricing.comps import (
    _remove_outliers, _calc_confidence, _classify, _chip_family,
    estimate, COMP_TABLE, CompEntry,
    TIER_EXACT, TIER_PARTIAL, TIER_BROAD, MIN_EXACT_COMPS,
)
from app.models import Listing, NormalizedIdentity, _utcnow


def _listing(brand="Nike", model="Test Shoe", category="shoes",
             condition="new", price=200.0, source_item_id=""):
    return Listing(
        id="t1", source="test", source_url="http://x",
        source_item_id=source_item_id or "t1",
        title=f"{brand} {model}", brand=brand, model=model,
        category=category, condition=condition, price=price, shipping=0.0,
        scraped_at=_utcnow(),
    )


def _identity(brand="Nike", model="Test Shoe", category="shoes",
              condition="new", **kwargs):
    return NormalizedIdentity(
        brand=brand, model=model, category=category,
        condition=condition, **kwargs,
    )


# ── Outlier removal ─────────────────────────────────────────────────

def test_outlier_removal_drops_extremes():
    prices = [100, 105, 110, 95, 102, 108, 500, 5]
    cleaned = _remove_outliers(prices)
    assert 500 not in cleaned and 5 not in cleaned


def test_outlier_removal_keeps_tight_cluster():
    prices = [100, 102, 105, 98, 103, 101, 99, 104]
    assert _remove_outliers(prices) == sorted(prices)


def test_outlier_removal_too_few():
    prices = [100, 200, 300]
    assert _remove_outliers(prices) == prices


# ── Chip family normalization ───────────────────────────────────────

def test_chip_family_extracts_apple_silicon():
    assert _chip_family("M1 Pro") == "m1 pro"
    assert _chip_family("m2 pro") == "m2 pro"
    assert _chip_family("M3 Max 12-core") == "m3 max"
    assert _chip_family("M5") == "m5"


def test_chip_family_handles_empty():
    assert _chip_family(None) == ""
    assert _chip_family("") == ""


# ── Confidence ──────────────────────────────────────────────────────

def test_confidence_higher_for_sold():
    prices = [100, 105, 110, 95, 102, 108, 103, 101, 99, 104]
    conf_sold = _calc_confidence(prices, 102, True, 0.8, 10)
    conf_active = _calc_confidence(prices, 102, False, 0.8, 10)
    assert conf_sold > conf_active


def test_confidence_capped_for_weak_match():
    prices = [100, 105, 110, 95, 102, 108, 103, 101, 99, 104]
    conf = _calc_confidence(prices, 102, True, 0.3, 10)
    assert conf <= 0.40


def test_confidence_capped_for_small_sample():
    prices = [100, 105, 110]
    conf = _calc_confidence(prices, 105, True, 0.9, 3)
    assert conf <= 0.55


# ── Tier classification: shoes ──────────────────────────────────────

def test_shoe_sku_match_is_exact():
    identity = _identity(size="10", sku="DZ5485-612")
    tier, _ = _classify(
        {"size": "11", "sku": "DZ5485-612"}, "Jordan 1 Chicago", identity)
    assert tier == TIER_EXACT


def test_shoe_size_mismatch_is_broad():
    identity = _identity(size="10")
    tier, reason = _classify({"size": "8"}, "Jordan 1 Sz 8", identity)
    assert tier == TIER_BROAD
    assert "size" in reason.lower()


def test_shoe_nearby_size_is_partial():
    identity = _identity(size="10")
    tier, _ = _classify({"size": "10.5"}, "Jordan 1 Sz 10.5", identity)
    assert tier == TIER_PARTIAL


# ── Tier classification: phones ─────────────────────────────────────

def test_phone_storage_match_is_exact():
    identity = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, carrier="unlocked", condition="good",
    )
    tier, _ = _classify(
        {"storage_gb": 256, "carrier": "unlocked"},
        "iPhone 14 Pro 256GB Unlocked", identity,
        item_brand="Apple", item_model="iPhone 14 Pro",
    )
    assert tier == TIER_EXACT


def test_phone_storage_mismatch_is_broad():
    identity = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, condition="good",
    )
    tier, _ = _classify(
        {"storage_gb": 128}, "iPhone 14 Pro 128GB", identity,
        item_brand="Apple", item_model="iPhone 14 Pro",
    )
    assert tier == TIER_BROAD


def test_phone_carrier_lock_difference_is_partial():
    identity = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, carrier="unlocked", condition="good",
    )
    tier, _ = _classify(
        {"storage_gb": 256, "carrier": "at&t"},
        "Apple iPhone 14 Pro 256GB AT&T",
        identity,
        item_brand="Apple", item_model="iPhone 14 Pro",
    )
    assert tier == TIER_PARTIAL


# ── Tier classification: laptops ────────────────────────────────────

def test_laptop_chip_generation_mismatch_is_broad():
    """Critical bug fix: M1 Pro vs M5 must be BROAD, not lumped together."""
    identity = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="M1 Pro", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, reason = _classify(
        {"cpu": "M5", "ram_gb": 16, "storage_gb": 512},
        "MacBook Pro 14 M5", identity,
    )
    assert tier == TIER_BROAD
    assert "chip" in reason.lower() or "generation" in reason.lower()


def test_laptop_same_chip_family_is_exact():
    identity = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="M2 Pro", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, _ = _classify(
        {"cpu": "M2 Pro", "ram_gb": 16, "storage_gb": 512},
        "MacBook Pro 14 M2 Pro 16/512", identity,
    )
    assert tier == TIER_EXACT


def test_laptop_ram_mismatch_is_broad():
    identity = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="M2", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, _ = _classify(
        {"cpu": "M2", "ram_gb": 8, "storage_gb": 512},
        "MacBook Pro 14 M2 8GB", identity,
    )
    assert tier == TIER_BROAD


def test_laptop_storage_mismatch_is_broad():
    identity = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="M2", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, _ = _classify(
        {"cpu": "M2", "ram_gb": 16, "storage_gb": 1024},
        "MacBook Pro 14 M2 16/1TB", identity,
    )
    assert tier == TIER_BROAD


# ── Comp key correctness ────────────────────────────────────────────

def test_laptop_comp_key_includes_chip_and_specs():
    """Must distinguish M1 Pro 16/512 from M5 24/1TB."""
    a = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="M1 Pro", ram_gb=16, storage_gb=512,
    )
    b = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="M5", ram_gb=24, storage_gb=1024,
    )
    assert a.comp_key != b.comp_key
    assert "m1 pro" in a.comp_key
    assert "m5" in b.comp_key


def test_phone_comp_key_includes_storage():
    a = NormalizedIdentity(
        brand="Apple", model="iPhone 15 Pro", category="phones",
        storage_gb=128,
    )
    b = NormalizedIdentity(
        brand="Apple", model="iPhone 15 Pro", category="phones",
        storage_gb=512,
    )
    assert a.comp_key != b.comp_key


# ── End-to-end with mock comps ──────────────────────────────────────

def test_estimate_returns_none_without_comps():
    listing = _listing(brand="Unknown", model="NoComps123")
    identity = _identity(brand="Unknown", model="NoComps123")
    assert estimate(listing, identity) is None


def test_estimate_with_mock_data(monkeypatch):
    monkeypatch.setattr("app.config.settings.use_mock_comps", True)
    key = "shoes|nike|tier test shoe"
    COMP_TABLE[key] = CompEntry(
        prices=[300, 310, 320, 330, 340],
        source="sold", spec={"size": "10"},
        titles=[f"Nike Tier Test Shoe Size 10 #{i}" for i in range(5)],
    )
    result = estimate(
        _listing(model="Tier Test Shoe"),
        _identity(model="Tier Test Shoe", size="10"),
    )
    assert result is not None
    assert result.match_quality > 0
    assert result.source == "sold"
