"""
Normalization router. Dispatches to category-specific normalizers.
"""
from __future__ import annotations
import logging
from ..models import Listing, NormalizedIdentity
from .shoes import normalize_shoes
from .phones import normalize_phones
from .laptops import normalize_laptops

log = logging.getLogger("normalize")

NORMALIZERS = {
    "shoes": normalize_shoes,
    "phones": normalize_phones,
    "laptops": normalize_laptops,
}


def _extract_aspects(raw: dict) -> dict[str, str]:
    """Extract eBay localizedAspects into a flat dict."""
    aspects = {}
    for aspect in raw.get("localizedAspects", []):
        name = aspect.get("name", "").lower().strip()
        value = aspect.get("value", "").strip()
        if name and value:
            aspects[name] = value
    return aspects


def normalize(listing: Listing) -> NormalizedIdentity:
    """
    Normalize a raw listing into structured identity fields.
    Uses category-specific normalizer if available, otherwise generic.
    """
    aspects = _extract_aspects(listing.raw)
    normalizer = NORMALIZERS.get(listing.category)

    if normalizer:
        identity = normalizer(listing, aspects)
    else:
        identity = NormalizedIdentity(
            brand=listing.brand or "",
            model=listing.model or "",
            category=listing.category,
            condition=listing.condition or "good",
        )

    # Ensure brand fallback from listing if normalizer didn't find one
    if not identity.brand and listing.brand:
        identity.brand = listing.brand
    if not identity.model and listing.model:
        identity.model = listing.model

    return identity
