"""
Search query builder.

Each query has:
  - terms (what to search for)
  - category (phones | shoes | laptops)
  - negative_terms (post-filter on results)
  - enabled flag (lets us turn whole categories on/off)

Phones are the priority category — most queries are phone-specific.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

log = logging.getLogger("queries")


@dataclass
class SearchQuery:
    terms: str
    category: str
    negative_terms: list[str] = field(default_factory=list)
    ebay_category_id: str = ""
    enabled: bool = True

    @property
    def query_string(self) -> str:
        """Final query string sent to eBay."""
        return self.terms

    def title_matches_negatives(self, title: str) -> bool:
        t = title.lower()
        return any(neg in t for neg in self.negative_terms)


EBAY_CATEGORY_IDS = {
    "shoes": "93427",
    "phones": "9355",
    "laptops": "177",
}


# ── Negative terms by category ──────────────────────────────────────
# Phone negatives are strict — these are the "junk" we want filtered post-fetch
PHONE_NEGATIVES = [
    # Accessories
    "case", "cover", "protector", "cable", "charger", "adapter",
    "stand", "mount", "holder", "screen protector", "tempered glass",
    "skin", "decal", "dock", "earbuds", "earphones", "headphones",
    "airpods", "magsafe", "wallet", "popsocket",
    # Damaged / unsellable
    "for parts", "spares", "spares or repair", "spares or repairs",
    "for repair", "repair", "faulty", "broken", "cracked",
    "smashed", "damaged", "won't turn on", "wont turn on", "no power",
    "no display", "no screen", "lcd issue", "screen issue",
    "icloud", "icloud locked", "find my", "activation lock",
    "blacklisted", "blocked imei", "bad imei",
    # Carrier-locked (we want unlocked only for now)
    "network locked", "carrier locked",
    # Finance / contract issues
    "finance", "on finance", "contract issue", "outstanding finance",
    # Replicas / refurbished housing
    "replica", "refurbished housing", "shell only", "frame only",
]

SHOE_NEGATIVES = [
    "case", "laces", "insole", "keychain", "sticker", "poster",
    "socks", "shoe tree", "cleaning", "crep", "sole protector",
    "replica", "rep ", "bootleg", "fake",
]

LAPTOP_NEGATIVES = [
    "case", "sleeve", "bag", "stand", "hub", "charger for",
    "keyboard cover", "screen protector", "cleaning", "skin",
    "webcam", "docking station", "for parts", "spares or repair",
    "faulty", "no power", "won't turn on", "wont turn on",
    "no display", "screen issue",
]

CATEGORY_NEGATIVES = {
    "shoes": SHOE_NEGATIVES,
    "phones": PHONE_NEGATIVES,
    "laptops": LAPTOP_NEGATIVES,
}


# ── Category enable switches ────────────────────────────────────────
# Disable a category to skip all its queries. Phones are the focus right now.
CATEGORY_ENABLED = {
    "phones": True,
    "shoes": True,         # left lightly enabled
    "laptops": True,       # left lightly enabled
}


# ── Query catalog ───────────────────────────────────────────────────
# Each entry: (terms, category, enabled).
# Spec-specific queries (with storage / "unlocked") help reduce junk and
# improve comp matching downstream.

_RAW_QUERIES: list[tuple[str, str, bool]] = [
    # ── PHONES (priority) ───────────────────────────────────────────
    # iPhone 13 Pro
    ("iPhone 13 Pro 128GB unlocked",      "phones", True),
    ("iPhone 13 Pro 256GB unlocked",      "phones", True),
    ("iPhone 13 Pro Max 128GB unlocked",  "phones", True),
    ("iPhone 13 Pro Max 256GB unlocked",  "phones", True),
    # iPhone 14 Pro
    ("iPhone 14 Pro 128GB unlocked",      "phones", True),
    ("iPhone 14 Pro 256GB unlocked",      "phones", True),
    ("iPhone 14 Pro Max 128GB unlocked",  "phones", True),
    ("iPhone 14 Pro Max 256GB unlocked",  "phones", True),
    # iPhone 15 Pro
    ("iPhone 15 Pro 128GB unlocked",      "phones", True),
    ("iPhone 15 Pro 256GB unlocked",      "phones", True),
    ("iPhone 15 Pro Max 256GB unlocked",  "phones", True),

    # ── SHOES (kept light, no expansion yet) ────────────────────────
    ("Air Jordan 1",                      "shoes", True),
    ("Nike Dunk Low",                     "shoes", True),

    # ── LAPTOPS (kept light, no expansion yet) ──────────────────────
    ("MacBook Pro 14",                    "laptops", True),
    ("MacBook Pro 16",                    "laptops", True),
]


def get_queries() -> list[SearchQuery]:
    """
    Build the active query list, respecting:
      - settings.categories_enabled (.env CATEGORIES_ENABLED, e.g. "phones")
      - the in-process CATEGORY_ENABLED dict (programmatic override)
      - the per-query enabled flag in _RAW_QUERIES
    """
    from .config import settings
    enabled_set = settings.enabled_categories
    queries = []
    for terms, category, enabled in _RAW_QUERIES:
        if not enabled:
            continue
        if not CATEGORY_ENABLED.get(category, True):
            continue
        if category not in enabled_set:
            continue
        q = SearchQuery(
            terms=terms,
            category=category,
            negative_terms=CATEGORY_NEGATIVES.get(category, []),
            ebay_category_id=EBAY_CATEGORY_IDS.get(category, ""),
            enabled=enabled,
        )
        queries.append(q)
    return queries


def add_query(terms: str, category: str, enabled: bool = True) -> None:
    _RAW_QUERIES.append((terms, category, enabled))


def set_category_enabled(category: str, enabled: bool) -> None:
    CATEGORY_ENABLED[category] = enabled
