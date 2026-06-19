"""
eBay Browse API source adapter.
Parsing logic moved to app/normalize/ — this just fetches and does minimal mapping.
"""
from __future__ import annotations
import hashlib
import logging
import time
from typing import Iterable
import httpx
from .base import BaseSource
from .ebay_auth import get_access_token
from ..config import settings
from ..models import Listing, _utcnow

log = logging.getLogger("ebay.source")

BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

EBAY_CATEGORIES = {
    "shoes": "93427", "phones": "9355", "laptops": "177",
}

CONDITION_MAP = {
    "New": "new", "New with tags": "new", "New with box": "new",
    "New without tags": "new", "New without box": "new",
    "New with defects": "like_new", "Open box": "like_new",
    "Certified - Refurbished": "like_new", "Excellent - Refurbished": "like_new",
    "Very Good - Refurbished": "good", "Good - Refurbished": "good",
    "Seller refurbished": "good", "Pre-owned": "good", "Used": "good",
    "For parts or not working": "parts",
}

KNOWN_BRANDS = ["Nike", "Adidas", "Apple", "Samsung", "Dell", "Lenovo",
                "HP", "Asus", "New Balance", "Puma", "Google", "OnePlus",
                "Sony", "Microsoft", "Jordan", "Converse"]


def _id(item_id: str) -> str:
    return hashlib.sha1(f"ebay:{item_id}".encode()).hexdigest()[:16]


class EbayBrowseSource(BaseSource):
    name = "ebay"

    def __init__(self, throttle_seconds: float = 1.0):
        self.throttle = throttle_seconds

    def fetch(self, query: str, category: str = "", limit: int = 50) -> Iterable[Listing]:
        try:
            token = get_access_token()
        except (ValueError, RuntimeError) as e:
            log.error("eBay auth failed: %s", e)
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace,
        }
        params: dict = {
            "q": query, "limit": min(limit, 200),
            "filter": "buyingOptions:{FIXED_PRICE}", "sort": "newlyListed",
        }
        cat_id = EBAY_CATEGORIES.get(category)
        if cat_id:
            params["category_ids"] = cat_id

        try:
            resp = httpx.get(BROWSE_URL, headers=headers, params=params, timeout=20)
        except httpx.TimeoutException:
            log.error("eBay API timeout for query=%s", query)
            return
        if resp.status_code != 200:
            log.error("eBay API error (%s): %s", resp.status_code, resp.text[:300])
            return

        items = resp.json().get("itemSummaries", [])
        log.info("eBay returned %d items for query='%s'", len(items), query)

        for item in items:
            try:
                listing = self._parse_item(item, category)
                if listing:
                    yield listing
            except Exception as e:
                log.warning("Failed to parse item %s: %s", item.get("itemId", "?"), e)
            if self.throttle > 0:
                time.sleep(self.throttle)

    def _parse_item(self, item: dict, category: str) -> Listing | None:
        price_val = float(item.get("price", {}).get("value", 0))
        if price_val <= 0:
            return None

        currency = item.get("price", {}).get("currency", "USD")
        shipping_cost = 0.0
        ship_opts = item.get("shippingOptions", [])
        if ship_opts:
            shipping_cost = float(ship_opts[0].get("shippingCost", {}).get("value", 0))

        cond_raw = item.get("condition", "")
        condition = CONDITION_MAP.get(cond_raw, "good")

        # Minimal brand extraction — normalizer does the real work
        aspects = {a.get("name", "").lower(): a.get("value", "")
                   for a in item.get("localizedAspects", [])}
        brand = aspects.get("brand", "")
        model = aspects.get("model", "")
        if not brand:
            title = item.get("title", "")
            for b in KNOWN_BRANDS:
                if b.lower() in title.lower():
                    brand = b
                    break

        seller_info = item.get("seller", {})
        feedback_pct = seller_info.get("feedbackPercentage", "")
        seller_rating = float(feedback_pct) / 100 if feedback_pct else None

        loc = item.get("itemLocation", {})
        location_parts = [loc.get("city", ""), loc.get("stateOrProvince", ""),
                          loc.get("country", "")]
        location = ", ".join(p for p in location_parts if p) or None

        buying_opts = item.get("buyingOptions", [])
        item_id = item.get("itemId", "")

        return Listing(
            id=_id(item_id),
            source="ebay",
            source_item_id=item_id,
            source_url=item.get("itemWebUrl", ""),
            title=item.get("title", ""),
            brand=brand, model=model,
            category=category or "other",
            spec={},  # normalizer fills this
            condition=condition,
            price=price_val, shipping=shipping_cost, currency=currency,
            location=location,
            seller=seller_info.get("username", ""),
            seller_rating=seller_rating,
            is_auction="AUCTION" in buying_opts,
            pickup_only="PERSONAL_OFFER" in buying_opts and "FIXED_PRICE" not in buying_opts,
            scraped_at=_utcnow(),
            raw=item,
        )
