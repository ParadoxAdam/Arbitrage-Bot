"""
Regression tests for bugs discovered in production data (Apr 25 dump):

1. Self-citation: candidate listing was included in its own comp set
2. Comp key too coarse: M1 Pro / M5 / M3 Max all bucketed under "macbook pro 14"

These tests exercise the fixes in v14.
"""
import pytest
from app.models import Listing, NormalizedIdentity, _utcnow
from app.normalize import normalize
from app.pricing.comps import _classify, TIER_EXACT, TIER_PARTIAL, TIER_BROAD


def _ebay_listing(title, source_item_id="abc123", category="laptops",
                  raw_aspects=None):
    raw = {
        "localizedAspects": [
            {"name": k, "value": v} for k, v in (raw_aspects or {}).items()
        ]
    }
    return Listing(
        id="t1", source="ebay", source_item_id=source_item_id,
        source_url=f"https://ebay.co.uk/itm/{source_item_id}",
        title=title, brand="Apple", category=category,
        price=644.15, shipping=0,
        scraped_at=_utcnow(), raw=raw,
    )


# ── Bug 1: Laptop normalizer captures chip generation ──────────────

def test_v14_macbook_normalizer_separates_m1_pro_from_m5():
    """The exact failure from production: M1 Pro Liquid Retina was
    miscategorised as just 'macbook pro 14'."""
    title = "Apple MacBook Pro 14 2021 M1 Pro Chip Liquid Retina 16GB SSD 512GB"
    l = _ebay_listing(title)
    identity = normalize(l)
    assert "m1" in identity.cpu.lower()
    assert "pro" in identity.cpu.lower()
    assert identity.ram_gb == 16
    assert identity.storage_gb == 512


def test_v14_macbook_m5_correctly_normalized():
    title = "APPLE MacBook Pro 14 (2025) - M5, 512 GB SSD"
    l = _ebay_listing(title)
    identity = normalize(l)
    # Catches M5 even though regex used to cap at M4
    assert identity.cpu and "m5" in identity.cpu.lower()


def test_v14_m1_pro_and_m5_have_different_comp_keys():
    """Critical: M1 Pro and M5 must NOT share a comp bucket."""
    m1 = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="m1 pro", ram_gb=16, storage_gb=512,
    )
    m5 = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="m5", ram_gb=16, storage_gb=512,
    )
    assert m1.comp_key != m5.comp_key


def test_v14_m1_pro_vs_m5_classified_as_broad():
    """If somehow both end up in the same fetch, classification rejects them."""
    target = NormalizedIdentity(
        brand="Apple", model="macbook pro 14", category="laptops",
        cpu="m1 pro", ram_gb=16, storage_gb=512, condition="good",
    )
    tier, reason = _classify(
        {"cpu": "m5", "ram_gb": 16, "storage_gb": 512},
        "MacBook Pro 14 M5 16/512", target,
    )
    assert tier == TIER_BROAD


# ── Bug 2: Self-citation ────────────────────────────────────────────

def test_v14_self_citation_excluded():
    """The candidate's own listing must never appear in its comp set."""
    from app.pricing.comps import CompResolution, _fetch_and_classify
    from unittest.mock import patch

    target_id = "326969420"
    listing = _ebay_listing(
        "MacBook Pro 14 M1 Pro 16GB 512GB",
        source_item_id=target_id,
    )
    identity = normalize(listing)

    fake_comp_items = [
        # First item is the target listing itself
        type('C', (), {
            'price': 644.15, 'title': "MacBook Pro 14 M1 Pro",
            'source_item_id': target_id, 'source_url': "",
            'spec': {'cpu': 'm1 pro', 'ram_gb': 16, 'storage_gb': 512},
            'condition': 'good', 'brand': 'Apple', 'model': 'macbook pro 14',
        })(),
        # Genuine comps
        type('C', (), {
            'price': 700.00, 'title': "MacBook Pro 14 M1 Pro #2",
            'source_item_id': "other-1", 'source_url': "",
            'spec': {'cpu': 'm1 pro', 'ram_gb': 16, 'storage_gb': 512},
            'condition': 'good', 'brand': 'Apple', 'model': 'macbook pro 14',
        })(),
        type('C', (), {
            'price': 720.00, 'title': "MacBook Pro 14 M1 Pro #3",
            'source_item_id': "other-2", 'source_url': "",
            'spec': {'cpu': 'm1 pro', 'ram_gb': 16, 'storage_gb': 512},
            'condition': 'good', 'brand': 'Apple', 'model': 'macbook pro 14',
        })(),
    ]

    # v15.3: fetcher now returns a CompFetchResult, not a list
    from app.pricing.ebay_comps import CompFetchResult
    fake_result = CompFetchResult()
    fake_result.items = fake_comp_items
    fake_result.raw_count = len(fake_comp_items)

    with patch('app.pricing.comps.settings.ebay_client_id', 'test'), \
         patch('app.pricing.ebay_comps.fetch_comp_items',
               return_value=fake_result):
        res = _fetch_and_classify(listing, identity)

    # The target's own ID should be excluded
    assert res.excluded_self == 1
    # Genuine comps make it through
    all_seen = res.exact + res.partial + res.broad
    seen_ids = [c.source_item_id for c in all_seen]
    assert target_id not in seen_ids
    assert "other-1" in seen_ids
    assert "other-2" in seen_ids
