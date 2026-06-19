"""Shoe normalizer — size, SKU, colorway, condition, model inference."""
from __future__ import annotations
import re
from ..models import Listing, NormalizedIdentity

KNOWN_BRANDS = ["Nike", "Adidas", "New Balance", "Puma", "Reebok",
                "Jordan", "Converse", "Vans", "Asics", "Yeezy",
                "Off-White", "Travis Scott", "Sacai"]

DS_KEYWORDS = ["ds ", " ds,", " ds.", "deadstock", "dead stock", "brand new",
               "bnib", "bnds", "dswt", "new with tags", "nwt",
               "new in box", "nib", "sealed"]
VNDS_KEYWORDS = ["vnds", "near deadstock", "worn once", "tried on", "tried-on"]

# Common colorway tokens — helps identify the specific release
COLORWAY_TOKENS = [
    "chicago", "bred", "shadow", "royal", "unc", "pine green",
    "turbo green", "lost and found", "satin", "mocha", "travis scott",
    "off-white", "dior", "concord", "cactus jack", "panda",
    "what the", "jubilee", "patent bred", "union", "chunky dunky",
    "white cement", "fire red", "true blue", "cool grey", "retro",
    "valentine", "black cement", "racer blue", "starfish",
    "navy", "yellow", "green", "red", "black", "white", "blue",
    "pink", "purple", "grey", "gray", "orange", "cream", "tan",
]

# Model patterns — try to extract model from title even if aspect missing
MODEL_PATTERNS = [
    r'(air jordan \d+(?:\.\d)?(?:\s+(?:low|mid|high))?)',
    r'(jordan \d+(?:\.\d)?(?:\s+(?:low|mid|high))?)',
    r'(dunk (?:low|high|mid)(?:\s+sb)?)',
    r'(dunk sb (?:low|high|mid))',
    r'(air force 1(?:\s+(?:low|mid|high))?)',
    r'(yeezy \w+\s*\d*)',
    r'(nike sb \w+)',
    r'(new balance \d{2,4})',
    r'(adidas \w+)',
]


def _parse_size_from_title(title: str) -> str:
    """Extract US shoe size from title."""
    patterns = [
        r'(?:size|sz)[:\s]+(\d{1,2}(?:\.5)?)(?!\d)',
        r'(?:us|men\'?s|women\'?s|gs)[:\s]+(?:size[:\s]+)?(\d{1,2}(?:\.5)?)(?!\d)',
        r'\b(\d{1,2}(?:\.5)?)\s*(?:us|m\b|w\b)',
    ]
    for p in patterns:
        m = re.search(p, title.lower())
        if m:
            val = m.group(1)
            # Sanity check: shoe sizes are 3-18 typically
            try:
                if 3 <= float(val) <= 18:
                    return val
            except ValueError:
                continue
    return ""


def _parse_sku_from_title(title: str) -> str:
    """Extract Nike/Jordan style code (e.g. DZ5485-612, 555088-101)."""
    patterns = [
        r'\b([A-Z]{1,3}\d{3,5}[-]\d{2,3})\b',
        r'\b(\d{6}[-]\d{3})\b',
    ]
    for p in patterns:
        m = re.search(p, title.upper())
        if m:
            return m.group(1)
    return ""


def _parse_colorway(title: str, aspect_color: str) -> str:
    """Extract colorway. Prefer known release names over generic colors."""
    title_l = title.lower()
    for token in COLORWAY_TOKENS:
        if token in title_l:
            return token
    return aspect_color.lower() if aspect_color else ""


def _parse_model(title: str, raw_model: str) -> str:
    """Extract shoe model. Prefer aspect but fall back to regex on title."""
    if raw_model and len(raw_model) > 3:
        return raw_model
    title_l = title.lower()
    for p in MODEL_PATTERNS:
        m = re.search(p, title_l)
        if m:
            return m.group(1).strip()
    return raw_model


def _parse_condition(title: str, raw_condition: str) -> str:
    title_l = " " + title.lower() + " "
    if any(k in title_l for k in DS_KEYWORDS):
        return "new"
    if any(k in title_l for k in VNDS_KEYWORDS):
        return "like_new"
    return raw_condition or "good"


def normalize_shoes(listing: Listing, aspects: dict[str, str]) -> NormalizedIdentity:
    brand = aspects.get("brand", listing.brand or "")
    raw_model = aspects.get("model", aspects.get("style", listing.model or ""))
    title = listing.title

    if not brand:
        for b in KNOWN_BRANDS:
            if b.lower() in title.lower():
                brand = b
                break

    model = _parse_model(title, raw_model)

    size = aspects.get("us shoe size", aspects.get("shoe size", ""))
    if not size:
        size = _parse_size_from_title(title)

    sku = aspects.get("style code", aspects.get("sku", ""))
    if not sku:
        sku = _parse_sku_from_title(title)

    colorway = _parse_colorway(title, aspects.get("color", ""))
    condition = _parse_condition(title, listing.condition or "")

    return NormalizedIdentity(
        brand=brand,
        model=model,
        category="shoes",
        size=size or None,
        sku=sku or None,
        colorway=colorway or None,
        condition=condition,
    )
