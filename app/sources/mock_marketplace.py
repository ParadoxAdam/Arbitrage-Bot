"""Mock marketplace — works without network. Used for dev/testing."""
from __future__ import annotations
import hashlib
from typing import Iterable
from .base import BaseSource
from ..models import Listing, _utcnow


def _id(source: str, url: str) -> str:
    return hashlib.sha1(f"{source}:{url}".encode()).hexdigest()[:16]


SAMPLES = [
    {"title": "Nike Air Jordan 1 Retro High OG Chicago 2022 Size 10 DS",
     "brand": "Nike", "model": "Air Jordan 1 Chicago 2022", "category": "shoes",
     "spec": {"size": "10", "sku": "DZ5485-612", "condition_tag": "DS"},
     "condition": "new", "price": 140.0, "shipping": 12.0,
     "seller": "kicks_seller", "seller_rating": 0.99},
    {"title": "Apple iPhone 14 Pro 256GB Deep Purple Unlocked",
     "brand": "Apple", "model": "iPhone 14 Pro", "category": "phones",
     "spec": {"storage": "256", "color": "Deep Purple", "carrier": "unlocked"},
     "condition": "like_new", "price": 570.0, "shipping": 0.0,
     "seller": "phone_store", "seller_rating": 0.98},
    {"title": "MacBook Pro 14 M2 Pro 16GB 512GB Space Gray w/ charger",
     "brand": "Apple", "model": "MacBook Pro 14 M2 Pro", "category": "laptops",
     "spec": {"cpu": "M2 Pro", "ram": "16", "storage": "512", "screen": "14",
              "charger_included": True},
     "condition": "good", "price": 715.0, "shipping": 16.0,
     "seller": "techreseller", "seller_rating": 0.97},
    {"title": "iPhone 13 128GB - ICLOUD LOCKED FOR PARTS",
     "brand": "Apple", "model": "iPhone 13", "category": "phones",
     "spec": {"storage": "128", "carrier": "locked"},
     "condition": "parts", "price": 70.0, "shipping": 8.0,
     "seller": "asis_dealer", "seller_rating": 0.85},
    {"title": "Lot of 5 used laptops - LOCAL PICKUP ONLY",
     "brand": "Mixed", "model": "Lot", "category": "laptops",
     "spec": {}, "condition": "fair", "price": 315.0, "shipping": 0.0,
     "seller": "estate_sale", "seller_rating": 0.80},
]


class MockMarketplace(BaseSource):
    name = "mock"

    def fetch(self, query: str = "", category: str = "", limit: int = 50) -> Iterable[Listing]:
        for i, s in enumerate(SAMPLES):
            if category and s["category"] != category:
                continue
            url = f"https://mock.local/listing/{i}"
            yield Listing(
                id=_id(self.name, url),
                source=self.name,
                source_item_id=f"mock-{i}",
                source_url=url,
                pickup_only="LOCAL PICKUP" in s["title"].upper(),
                is_auction=False,
                scraped_at=_utcnow(),
                raw=s,
                **s,
            )
