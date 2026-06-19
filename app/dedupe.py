"""
Deduplication — two layers:

1. Exact dedupe: source + source_item_id (or source_url).
   Same listing from same source = always a dupe. DB-backed.

2. Similarity dedupe: (category, brand, model, spec, seller, price).
   Cross-source fuzzy match. Separate from exact.
"""
from __future__ import annotations
import hashlib
import logging
from .models import Listing, ListingRow
from .db import session_scope

log = logging.getLogger("dedupe")


def exact_dedupe_key(listing: Listing) -> str:
    """
    Primary dedupe key based on source + source item ID.
    Two eBay listings with the same itemId are always the same listing.
    """
    key = f"{listing.source}:{listing.source_item_id or listing.source_url}"
    return hashlib.sha1(key.encode()).hexdigest()


def similarity_hash(listing: Listing) -> str:
    """
    Fuzzy dedupe hash for cross-source matching.
    Same item listed on eBay and another platform would have different
    exact keys but similar hashes.
    """
    key = "|".join([
        listing.category,
        (listing.brand or "").lower().strip(),
        (listing.model or "").lower().strip(),
        str(sorted(listing.spec.items())),
        f"{round(listing.price, 0)}",
    ])
    return hashlib.sha1(key.encode()).hexdigest()


def is_duplicate(listing: Listing) -> bool:
    """
    Check if we've already seen this exact listing (DB-backed).
    Returns True if duplicate, False if new.
    """
    key = exact_dedupe_key(listing)
    with session_scope() as s:
        exists = s.query(ListingRow).filter_by(dedupe_key=key).first()
        return exists is not None
