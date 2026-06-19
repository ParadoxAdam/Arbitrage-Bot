"""
eBay live comp fetcher with full junk filtering.

Comp pool hygiene rules (v15.3):
  1. Apply the SAME negative keywords as the main candidate pipeline
     (cases, locked phones, parts-only, etc.)
  2. Apply category price floors to drop implausibly cheap listings
     (a £7 "iPhone 13 Pro" is an accessory in disguise)
  3. Apply product-type validation — title must look like the actual product,
     not an accessory/poster/box-only/screen-replacement etc.
  4. Track every drop reason for debug + dashboard surfacing
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any
import httpx
from ..config import settings
from ..sources.ebay_auth import get_access_token
from ..models import Listing, _utcnow

log = logging.getLogger("ebay.comps")

BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


# ── Negative keywords by category ───────────────────────────────────
# Mirrored from app/queries.py PHONE_NEGATIVES / SHOE_NEGATIVES / LAPTOP_NEGATIVES
# but used for filtering COMP results, not main listings.
# Kept here so comp filtering doesn't depend on importing query config.

PHONE_COMP_NEGATIVES = [
    # Accessories
    "case", "cover", "screen protector", "tempered glass",
    "cable", "charger", "adapter", "stand", "mount", "holder",
    "skin", "decal", "dock", "earbuds", "earphones", "headphones",
    "airpods", "magsafe", "wallet case", "popsocket", "lanyard",
    # Damaged / broken / parts
    "for parts", "spares", "spares or repair", "spares or repairs",
    "for repair", "faulty", "broken", "cracked", "smashed",
    "damage", "damaged",                    # v15.4.3: was missing bare "damage"
    "with damage", "has damage", "any damage", "minor damage",
    "cosmetic damage", "cracked back", "back glass damage",
    "won't turn on", "wont turn on", "no power",
    "no display", "no screen", "lcd issue", "screen issue",
    "scratched screen", "scratch on screen", "dent",
    "reparable", "repairable",              # v15.4.3: damaged-but-fixable signal
    "read description", "read desc",        # v15.4.3: known seller code for "issue inside"
    "as is", "as-is", "as described",
    # Battery health degradation (v15.4.3) — these signal known battery problems
    " 7%", " 70%", " 71%", " 72%", " 73%", " 74%",
    " 75%", " 76%", " 77%", " 78%", " 79%",
    " 80%", " 81%", " 82%", " 83%", " 84%",
    " 85%", " 86%", " 87%", " 88%", " 89%",
    "battery service", "service battery", "degraded battery",
    # Locked / blacklisted
    "icloud", "icloud locked", "find my", "activation lock",
    "activation locked", "blacklisted", "blocked imei", "bad imei",
    "network locked", "carrier locked",
    # Finance issues
    "finance", "on finance", "outstanding finance", "contract issue",
    # Shells / parts only
    "replica", "refurbished housing", "shell only", "frame only",
    "screen only", "lcd assembly", "back glass", "battery only",
    "logic board", "motherboard only", "no battery",
    # Box only / packaging
    "box only", "empty box", "packaging only", "documentation only",
    # Posters / merch / collectibles
    "poster", "keyring", "keychain", "sticker", "miniature", "model toy",
]

SHOE_COMP_NEGATIVES = [
    # Accessories
    "case", "laces", "lace", "insole", "insoles", "shoe tree", "shoe horn",
    "keychain", "keyring", "sticker", "poster", "print",
    "miniature", "miniatures", "model", "figurine", "ornament",
    "socks", "cleaning", "crep", "sole protector", "shoe rack",
    # Replicas
    "replica", "rep ", " rep,", "bootleg", "fake", "1:1", "unauthorized",
    # Box / merch only
    "box only", "empty box", "shoebox only", "shoe box only",
    "t-shirt", "tshirt", "hoodie", "tee", "shirt only",
    "playing card", "trading card", "lego",
]

LAPTOP_COMP_NEGATIVES = [
    "case", "sleeve", "bag for", "stand", "hub", "charger for",
    "charger only", "keyboard cover", "screen protector", "skin",
    "webcam cover", "docking station",
    # Damaged / parts
    "for parts", "spares", "spares or repair", "faulty", "broken",
    "cracked", "no power", "won't turn on", "wont turn on",
    "no display", "screen issue", "lcd assembly", "lcd only",
    "screen replacement", "screen only", "battery only", "battery replacement",
    "logic board", "motherboard only", "keyboard only",
    "shell only", "case only", "housing only",
    # Box only
    "box only", "empty box",
    # Replicas
    "replica", "fake",
]

CATEGORY_COMP_NEGATIVES = {
    "phones": PHONE_COMP_NEGATIVES,
    "shoes": SHOE_COMP_NEGATIVES,
    "laptops": LAPTOP_COMP_NEGATIVES,
}


# ── Category price floors for comp filtering ───────────────────────
# A "working iPhone 13 Pro" comp under £80 is implausible — almost
# Comp price floors. Below this, a comp is rejected as implausibly cheap.
# Conservative — too high rejects legitimate cheap listings.
COMP_PRICE_FLOORS = {
    "phones": 80.0,    # GBP — generic phone floor (legacy/budget models)
    "laptops": 130.0,
    "shoes": 35.0,
}

# Higher floors for specific product families. v15.4.3: an iPhone Pro at
# £115 is almost certainly damaged — even on the cheap end of healthy
# 13 Pro 128GB unlocked, you're £250+. These floors catch broken units
# that have clean titles (no "damaged" / "for parts" tokens).
PRODUCT_AWARE_FLOORS = {
    # iPhone Pro family — broken units cluster around £100-150
    "iphone 13 pro": 200.0,
    "iphone 13 pro max": 230.0,
    "iphone 14 pro": 280.0,
    "iphone 14 pro max": 330.0,
    "iphone 15 pro": 350.0,
    "iphone 15 pro max": 450.0,
    "iphone 16 pro": 450.0,
    "iphone 16 pro max": 550.0,
    # MacBook Pro family
    "macbook pro 14": 600.0,
    "macbook pro 16": 800.0,
}


def _resolve_price_floor(category: str, query: str, title: str) -> float:
    """
    Return the highest applicable price floor for this comp.
    Checks both the search query (what we're looking for) and the comp's
    own title — both should suggest the same product family.
    """
    base = COMP_PRICE_FLOORS.get(category, 0)
    haystack = f"{query} {title}".lower()
    for product, floor in PRODUCT_AWARE_FLOORS.items():
        if product in haystack:
            base = max(base, floor)
    return base


# ── Product-type validation tokens ──────────────────────────────────
# A valid phone comp title should mention an iPhone or other phone token.
# Used as a final sanity check after negative keyword filtering.
PHONE_PRODUCT_TOKENS = [
    "iphone", "galaxy", "pixel", "oneplus", "xiaomi", "redmi",
    "huawei", "honor", "motorola", "moto", "sony xperia",
]

LAPTOP_PRODUCT_TOKENS = [
    "macbook", "thinkpad", "xps", "surface laptop", "surface pro",
    "surface book", "rog", "zephyrus", "blade", "razer",
    "ideapad", "yoga", "latitude", "inspiron", "envy", "spectre",
    "zenbook", "vivobook", "predator", "nitro",
]

SHOE_PRODUCT_TOKENS = [
    # Sneakers / trainers — generic terms that should appear in a shoe listing
    "shoe", "shoes", "sneaker", "sneakers", "trainer", "trainers",
    "boot", "boots",
    # Common model families
    "jordan", "dunk", "air force", "air max", "yeezy", "samba",
    "stan smith", "ultra boost", "550", "990", "574",
]

CATEGORY_PRODUCT_TOKENS = {
    "phones": PHONE_PRODUCT_TOKENS,
    "shoes": SHOE_PRODUCT_TOKENS,
    "laptops": LAPTOP_PRODUCT_TOKENS,
}


# ── Comp pool sanity rules ──────────────────────────────────────────
# If too few comps survive filtering, the pool is unreliable.
MIN_VALID_COMPS = 3
# If most comps got filtered out as junk, the search itself was bad.
MIN_VALID_RATIO = 0.20      # at least 20% of raw must survive


# ── Models ──────────────────────────────────────────────────────────

@dataclass
class CompItem:
    """A single comp listing with price + parsed identity."""
    price: float
    title: str
    spec: dict[str, Any]
    condition: str
    brand: str
    model: str
    source_item_id: str = ""
    source_url: str = ""


@dataclass
class CompFetchResult:
    """Full diagnostic record from a comp fetch.

    Tells the comp engine what got filtered and why, so it can decide
    whether the comp pool is trustworthy enough to value against.
    """
    items: list[CompItem] = field(default_factory=list)
    raw_count: int = 0
    dropped_negative: int = 0
    dropped_price_floor: int = 0
    dropped_no_product_token: int = 0
    dropped_multi_variant: int = 0       # v15.4.7
    dropped_zero_price: int = 0
    pool_rejected: bool = False
    rejection_reason: str = ""

    # Sample drops for debugging (first 5 of each)
    sample_negative_drops: list[tuple[float, str]] = field(default_factory=list)
    sample_floor_drops: list[tuple[float, str]] = field(default_factory=list)
    sample_token_drops: list[tuple[float, str]] = field(default_factory=list)
    sample_multi_variant_drops: list[tuple[float, str]] = field(default_factory=list)

    def debug_summary(self) -> str:
        """One-line summary for logging."""
        return (
            f"raw={self.raw_count} "
            f"valid={len(self.items)} "
            f"neg_dropped={self.dropped_negative} "
            f"floor_dropped={self.dropped_price_floor} "
            f"multi_dropped={self.dropped_multi_variant} "
            f"token_dropped={self.dropped_no_product_token}"
            + (f" REJECTED: {self.rejection_reason}" if self.pool_rejected else "")
        )


# ── Filters ─────────────────────────────────────────────────────────

import re as _re_match

# Compile word-boundary regexes per category, lazily
_NEG_REGEX_CACHE: dict[str, list[tuple[str, "_re_match.Pattern"]]] = {}


def _build_negative_regexes(category: str) -> list[tuple[str, "_re_match.Pattern"]]:
    """
    Build word-boundary regexes for a category's negative keywords.

    Word boundaries match across most punctuation (parens, dashes, slashes,
    commas) which is what we want for messy eBay titles like
    "(Unlocked)Broken Back" or "13Pro,iCloud Locked".

    Multi-word keywords (e.g. "for parts") are matched as substrings —
    they're already specific enough that "casey" / "broken back" style
    false positives aren't an issue.

    Numeric tokens with a leading space (e.g. " 86%") are left as
    substring matches because regex word boundaries don't fire for them.
    """
    if category in _NEG_REGEX_CACHE:
        return _NEG_REGEX_CACHE[category]

    out: list[tuple[str, "_re_match.Pattern"]] = []
    for kw in CATEGORY_COMP_NEGATIVES.get(category, []):
        kw_l = kw.lower()
        # Numeric/percent tokens — substring match (no regex)
        if kw_l.startswith(" ") or "%" in kw_l:
            out.append((kw, None))   # type: ignore[arg-type]
            continue
        if " " in kw_l or "-" in kw_l or "'" in kw_l:
            # Multi-token — substring match
            out.append((kw, None))   # type: ignore[arg-type]
            continue
        # Single token — word-boundary regex
        pattern = _re_match.compile(rf"\b{_re_match.escape(kw_l)}\b")
        out.append((kw, pattern))
    _NEG_REGEX_CACHE[category] = out
    return out


def _matches_negative_keyword(title: str, category: str) -> str | None:
    """Return the matching keyword if title hits a negative; else None."""
    title_l = title.lower()
    for kw, pat in _build_negative_regexes(category):
        if pat is None:
            # Substring match
            if kw.lower() in title_l:
                return kw
        else:
            if pat.search(title_l):
                return kw
    return None


def _has_product_token(title: str, category: str) -> bool:
    """Title must contain at least one product-family token."""
    tokens = CATEGORY_PRODUCT_TOKENS.get(category, [])
    if not tokens:
        return True
    title_l = title.lower()
    return any(tok in title_l for tok in tokens)


def _below_price_floor(price: float, category: str,
                        query: str = "", title: str = "") -> bool:
    floor = _resolve_price_floor(category, query, title)
    return floor > 0 and price < floor


# v15.4.7 — multi-variant detection (uses the same logic as comps.py)
def _is_multi_variant_listing(title: str) -> bool:
    """True if a comp's title indicates it's a multi-variant placeholder
    listing whose price represents only the cheapest variant."""
    from .comps import _is_multi_variant
    return _is_multi_variant(title)


# ── Main fetch ──────────────────────────────────────────────────────

def fetch_comp_items(
    query: str,
    category: str,
    category_id: str = "",
    limit: int = 40,
) -> CompFetchResult:
    """
    Fetch active eBay listings as comp items, with full hygiene filtering.

    Returns a CompFetchResult with diagnostic counters so callers can
    distinguish "no comps available" from "comp pool was too polluted to use".
    """
    result = CompFetchResult()

    if not settings.ebay_client_id:
        return result

    try:
        token = get_access_token()
    except (ValueError, RuntimeError) as e:
        log.error("eBay auth failed: %s", e)
        return result

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": settings.ebay_marketplace,
    }
    params: dict = {
        "q": query,
        "limit": min(limit, 200),
        "filter": "buyingOptions:{FIXED_PRICE}",
        # v15.4.3: removed "sort": "price" which biased toward the cheapest
        # listings (almost all damaged/parts). Default eBay relevance sort
        # gives a more representative cross-section of conditions.
    }
    if category_id:
        params["category_ids"] = category_id

    try:
        resp = httpx.get(BROWSE_URL, headers=headers, params=params, timeout=20)
    except httpx.TimeoutException:
        log.error("eBay comps timeout: query=%s", query)
        return result

    if resp.status_code != 200:
        log.error("eBay comps error (%s)", resp.status_code)
        return result

    items = resp.json().get("itemSummaries", [])
    result.raw_count = len(items)

    if not items:
        return result

    # Process every item through the full filter chain
    from ..normalize import normalize

    for item in items:
        price_info = item.get("price", {})
        price = float(price_info.get("value", 0))
        title = item.get("title", "")

        if price <= 0 or not title:
            result.dropped_zero_price += 1
            continue

        # 1. Negative-keyword filter
        neg = _matches_negative_keyword(title, category)
        if neg:
            result.dropped_negative += 1
            if len(result.sample_negative_drops) < 5:
                result.sample_negative_drops.append(
                    (price, f"[{neg}] {title[:70]}")
                )
            continue

        # 2. Product-type validation
        if not _has_product_token(title, category):
            result.dropped_no_product_token += 1
            if len(result.sample_token_drops) < 5:
                result.sample_token_drops.append((price, title[:80]))
            continue

        # 3. Price-floor sanity check (product-aware as of v15.4.3)
        if _below_price_floor(price, category, query=query, title=title):
            result.dropped_price_floor += 1
            if len(result.sample_floor_drops) < 5:
                result.sample_floor_drops.append((price, title[:80]))
            continue

        # 4. Multi-variant filter (v15.4.7)
        # Listings titled "All Colours / All Sizes / 128/256/512GB" price
        # at the cheapest variant — they're not exact comps.
        if _is_multi_variant_listing(title):
            result.dropped_multi_variant += 1
            if len(result.sample_multi_variant_drops) < 5:
                result.sample_multi_variant_drops.append((price, title[:80]))
            continue

        # Survived all filters — normalize and keep
        aspects = {a.get("name", "").lower(): a.get("value", "")
                   for a in item.get("localizedAspects", [])}
        brand = aspects.get("brand", "")
        model = aspects.get("model", "")
        cond_raw = item.get("condition", "Used")

        mini_listing = Listing(
            id="comp", source="ebay", source_item_id=item.get("itemId", ""),
            source_url="", title=title, brand=brand, model=model,
            category=category, price=price, shipping=0,
            condition=_simplify_condition(cond_raw),
            scraped_at=_utcnow(), raw=item,
        )
        identity = normalize(mini_listing)

        result.items.append(CompItem(
            price=price, title=title,
            spec=identity.spec_dict,
            condition=identity.condition,
            brand=identity.brand,
            model=identity.model,
            source_item_id=item.get("itemId", ""),
            source_url=item.get("itemWebUrl", ""),
        ))

    # ── Pool sanity checks ──────────────────────────────────────────
    if len(result.items) < MIN_VALID_COMPS:
        result.pool_rejected = True
        result.rejection_reason = (
            f"only {len(result.items)} valid comps after filtering "
            f"(need {MIN_VALID_COMPS}); raw={result.raw_count}, "
            f"dropped: neg={result.dropped_negative}, "
            f"floor={result.dropped_price_floor}, "
            f"token={result.dropped_no_product_token}"
        )

    elif result.raw_count > 0:
        valid_ratio = len(result.items) / result.raw_count
        if valid_ratio < MIN_VALID_RATIO:
            result.pool_rejected = True
            result.rejection_reason = (
                f"valid ratio {valid_ratio:.0%} below {MIN_VALID_RATIO:.0%} — "
                f"comp pool is mostly junk ({result.dropped_negative + result.dropped_price_floor + result.dropped_no_product_token} of {result.raw_count} dropped). "
                f"Search term likely too generic."
            )

    log.info("eBay comps: query='%s' -> %s",
             query, result.debug_summary())
    return result


def _simplify_condition(raw: str) -> str:
    raw_l = raw.lower()
    if "new" in raw_l:
        return "new"
    if "open box" in raw_l or "refurbished" in raw_l:
        return "like_new"
    if "parts" in raw_l or "not working" in raw_l:
        return "parts"
    return "good"
