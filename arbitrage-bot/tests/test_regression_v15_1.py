"""
Regression tests for v15.1 patches:
1. Phone family check: iPhone 14 Pro vs iPhone 14 Pro Max must be BROAD
2. Generation check: iPhone 14 Pro vs iPhone 15 Pro must be BROAD
3. Brand mismatch is BROAD across categories
4. Candidate unlocked + comp carrier unknown = PARTIAL (not exact)
5. MacBook Air vs MacBook Pro is BROAD
6. Near-miss tracking populates fail_reason
"""
import pytest
from app.models import NormalizedIdentity
from app.pricing.comps import (
    _classify, _phone_family, _macbook_family,
    TIER_EXACT, TIER_PARTIAL, TIER_BROAD,
    add_near_miss, get_near_misses, reset_near_misses, NearMiss,
)


def test_phone_family_extracts_iphone_pro_vs_pro_max():
    assert _phone_family("Apple iPhone 14 Pro Max 256GB") == "iphone 14 pro max"
    assert _phone_family("iPhone 14 Pro 256GB") == "iphone 14 pro"
    assert _phone_family("iPhone 14") == "iphone 14"


def test_phone_family_handles_messy_input():
    assert _phone_family("Apple iPhone 15 Pro - 128GB") == "iphone 15 pro"
    assert _phone_family("iphone 14 PRO MAX brand new") == "iphone 14 pro max"


def test_iphone_14_pro_vs_pro_max_is_broad():
    """Critical v15.1 bug fix."""
    target = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, carrier="unlocked", condition="good",
    )
    tier, reason = _classify(
        {"storage_gb": 256, "carrier": "unlocked"},
        "Apple iPhone 14 Pro Max 256GB Unlocked",
        target,
        item_brand="Apple", item_model="iPhone 14 Pro Max",
    )
    assert tier == TIER_BROAD
    assert "different family" in reason


def test_iphone_14_pro_vs_iphone_15_pro_is_broad():
    target = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, carrier="unlocked", condition="good",
    )
    tier, _ = _classify(
        {"storage_gb": 256, "carrier": "unlocked"},
        "Apple iPhone 15 Pro 256GB Unlocked",
        target,
        item_brand="Apple", item_model="iPhone 15 Pro",
    )
    assert tier == TIER_BROAD


def test_iphone_unlocked_with_unknown_carrier_comp_is_partial():
    """v15.1: candidate unlocked + comp carrier unknown → can't assume same."""
    target = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, carrier="unlocked", condition="good",
    )
    tier, reason = _classify(
        {"storage_gb": 256},  # NO carrier info
        "Apple iPhone 14 Pro 256GB",
        target,
        item_brand="Apple", item_model="iPhone 14 Pro",
    )
    assert tier == TIER_PARTIAL
    assert "carrier" in reason.lower()


def test_iphone_both_unlocked_is_exact():
    target = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, carrier="unlocked", condition="good",
    )
    tier, _ = _classify(
        {"storage_gb": 256, "carrier": "unlocked"},
        "Apple iPhone 14 Pro 256GB Unlocked",
        target,
        item_brand="Apple", item_model="iPhone 14 Pro",
    )
    assert tier == TIER_EXACT


def test_brand_mismatch_is_broad():
    """Apple iPhone vs Samsung Galaxy must reject regardless of specs."""
    target = NormalizedIdentity(
        brand="Apple", model="iPhone 14 Pro", category="phones",
        storage_gb=256, condition="good",
    )
    tier, reason = _classify(
        {"storage_gb": 256},
        "Samsung Galaxy S23 256GB",
        target,
        item_brand="Samsung", item_model="Galaxy S23",
    )
    assert tier == TIER_BROAD
    assert "brand" in reason.lower()


def test_macbook_air_vs_macbook_pro_is_broad():
    target = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="m2 pro", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, reason = _classify(
        {"cpu": "m2", "ram_gb": 16, "storage_gb": 512},
        "Apple MacBook Air 13 M2 16GB 512GB",
        target,
        item_brand="Apple", item_model="macbook air 13",
    )
    assert tier == TIER_BROAD


def test_macbook_pro_14_vs_16_is_broad():
    target = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="m2 pro", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, _ = _classify(
        {"cpu": "m2 pro", "ram_gb": 16, "storage_gb": 512},
        "Apple MacBook Pro 16 M2 Pro 16GB 512GB",
        target,
        item_brand="Apple", item_model="macbook pro 16",
    )
    assert tier == TIER_BROAD


def test_macbook_family_extraction():
    assert _macbook_family("Apple MacBook Pro 14 2021") == "macbook pro 14"
    assert _macbook_family("MacBook Air 13 M2") == "macbook air 13"
    assert _macbook_family("MacBook Pro 16 M3 Max") == "macbook pro 16"


def test_near_miss_tracking():
    reset_near_misses()
    assert get_near_misses() == []

    add_near_miss(NearMiss(
        title="Test Item", url="http://x", price=100, expected_resale=200,
        net_profit=80, roi=0.8, score=0.45, confidence=0.5,
        match_quality=0.7, comp_source="active", comp_count=10,
        category="phones", fail_reason="confidence too low",
    ))
    misses = get_near_misses(limit=5)
    assert len(misses) == 1
    assert misses[0].fail_reason == "confidence too low"
    assert misses[0].to_dict()["title"] == "Test Item"
