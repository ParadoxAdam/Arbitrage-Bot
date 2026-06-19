"""
v15.5.5 — Top Failed tab dedupe by URL.

The same eBay listing was producing multiple near-miss records when:
  (a) it matched multiple search queries within one scan, OR
  (b) it was rescored across consecutive scans (genuine near-miss recheck).

Add-near-miss now drops prior entries with the same URL.
"""
from app.pricing.comps import (
    NearMiss, add_near_miss, get_near_misses, reset_near_misses,
)


def _miss(url, score=0.4, expected_resale=200):
    return NearMiss(
        title=f"listing-{url}", url=url, price=100, shipping=0,
        expected_resale=expected_resale, net_profit=50, roi=0.5,
        score=score, confidence=0.5, match_quality=0.85,
        comp_source="active", comp_count=10, category="phones",
        fail_reason="x",
    )


def test_same_url_dedupes_keeping_latest():
    reset_near_misses()
    add_near_miss(_miss("http://ebay.com/itm/123", score=0.4, expected_resale=200))
    add_near_miss(_miss("http://ebay.com/itm/123", score=0.45, expected_resale=210))
    add_near_miss(_miss("http://ebay.com/itm/123", score=0.43, expected_resale=205))

    misses = get_near_misses()
    assert len(misses) == 1
    # Latest entry wins (the third one we added)
    assert misses[0].score == 0.43
    assert misses[0].expected_resale == 205


def test_different_urls_kept_separately():
    reset_near_misses()
    add_near_miss(_miss("http://ebay.com/itm/123", score=0.4))
    add_near_miss(_miss("http://ebay.com/itm/456", score=0.5))
    add_near_miss(_miss("http://ebay.com/itm/789", score=0.3))

    misses = get_near_misses()
    assert len(misses) == 3
    urls = {m.url for m in misses}
    assert urls == {
        "http://ebay.com/itm/123",
        "http://ebay.com/itm/456",
        "http://ebay.com/itm/789",
    }


def test_empty_url_does_not_dedupe():
    """Edge case: NearMiss with no URL shouldn't collapse multiple entries
    onto each other (e.g. mock listings)."""
    reset_near_misses()
    add_near_miss(_miss("", score=0.4))
    add_near_miss(_miss("", score=0.5))
    misses = get_near_misses()
    assert len(misses) == 2


def test_reset_clears_dedupe_state():
    reset_near_misses()
    add_near_miss(_miss("http://x", score=0.4))
    reset_near_misses()
    add_near_miss(_miss("http://x", score=0.5))
    misses = get_near_misses()
    assert len(misses) == 1
    assert misses[0].score == 0.5
