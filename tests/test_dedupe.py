"""Tests for deduplication."""
from app.models import Listing, _utcnow
from app.dedupe import exact_dedupe_key, similarity_hash


def _listing(source="ebay", item_id="123", price=100.0):
    return Listing(
        id="t1", source=source, source_item_id=item_id,
        source_url=f"https://{source}.com/{item_id}",
        title="Test", category="shoes", price=price, shipping=0,
        scraped_at=_utcnow(),
    )


def test_exact_dedupe_same_source_same_id():
    a = _listing(source="ebay", item_id="ABC123")
    b = _listing(source="ebay", item_id="ABC123")
    assert exact_dedupe_key(a) == exact_dedupe_key(b)


def test_exact_dedupe_different_source():
    a = _listing(source="ebay", item_id="ABC123")
    b = _listing(source="stockx", item_id="ABC123")
    assert exact_dedupe_key(a) != exact_dedupe_key(b)


def test_similarity_hash_same_item():
    a = _listing(price=100)
    b = _listing(price=100)
    assert similarity_hash(a) == similarity_hash(b)


def test_similarity_hash_different_price():
    a = _listing(price=100)
    b = _listing(price=200)
    assert similarity_hash(a) != similarity_hash(b)
