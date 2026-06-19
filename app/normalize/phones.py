"""Phone normalizer — storage, carrier, color, model inference, condition."""
from __future__ import annotations
import re
from ..models import Listing, NormalizedIdentity

KNOWN_BRANDS = ["Apple", "Samsung", "Google", "OnePlus", "Sony",
                "Motorola", "LG", "Xiaomi", "Huawei", "Nothing"]

STORAGE_PATTERN = re.compile(r'(\d{2,4})\s*(?:gb|GB|tb|TB)', re.IGNORECASE)
TB_PATTERN = re.compile(r'(\d)\s*(?:tb|TB)', re.IGNORECASE)

UNLOCKED_KEYWORDS = ["unlocked", "factory unlocked", "gsm unlocked", "sim free"]
LOCKED_KEYWORDS = ["at&t", "t-mobile", "tmobile", "verizon", "vzw",
                   "sprint", "cricket", "metro pcs", "metropcs",
                   "carrier locked", "network locked"]

MODEL_PATTERNS = [
    # IMPORTANT: alternation order matters — regex is greedy left-to-right.
    # "pro max" must come BEFORE "pro" or "iPhone 15 Pro Max" parses as "iPhone 15 Pro".
    r'(iphone \d{1,2}(?:\s+(?:pro max|plus|mini|pro|se))?)',
    r'(galaxy s\d{1,2}(?:\s+(?:ultra|plus|fe))?)',
    r'(galaxy note\s*\d{1,2})',
    r'(galaxy z (?:flip|fold)\s*\d?)',
    r'(pixel \d(?:\s+(?:pro xl|pro|xl|a))?)',
    r'(oneplus \d+(?:\s+(?:pro|t))?)',
]


def _parse_storage(title: str, aspects: dict) -> int | None:
    raw = aspects.get("storage capacity", "")
    if raw:
        if "tb" in raw.lower():
            m = re.search(r'(\d+)', raw)
            if m:
                return int(m.group(1)) * 1024
        m = re.search(r'(\d+)', raw)
        if m:
            return int(m.group(1))

    tb_match = TB_PATTERN.search(title)
    if tb_match:
        return int(tb_match.group(1)) * 1024

    m = STORAGE_PATTERN.search(title)
    if m:
        val = int(m.group(1))
        if 16 <= val <= 2048:
            return val
    return None


def _parse_carrier(title: str, aspects: dict) -> str:
    raw = aspects.get("network", aspects.get("carrier", "")).lower()
    if raw:
        if "unlocked" in raw or "sim free" in raw:
            return "unlocked"
        return raw

    title_l = title.lower()
    if any(k in title_l for k in UNLOCKED_KEYWORDS):
        return "unlocked"
    for k in LOCKED_KEYWORDS:
        if k in title_l:
            return k.replace("tmobile", "t-mobile").replace("vzw", "verizon")
    return ""


def _parse_model(title: str, raw_model: str) -> str:
    if raw_model and len(raw_model) > 3:
        return raw_model
    title_l = title.lower()
    for p in MODEL_PATTERNS:
        m = re.search(p, title_l)
        if m:
            return m.group(1).strip()
    return raw_model


def normalize_phones(listing: Listing, aspects: dict[str, str]) -> NormalizedIdentity:
    brand = aspects.get("brand", listing.brand or "")
    raw_model = aspects.get("model", listing.model or "")
    title = listing.title

    if not brand:
        for b in KNOWN_BRANDS:
            if b.lower() in title.lower():
                brand = b
                break

    model = _parse_model(title, raw_model)
    storage = _parse_storage(title, aspects)
    carrier = _parse_carrier(title, aspects)
    colorway = aspects.get("color", "")

    return NormalizedIdentity(
        brand=brand,
        model=model,
        category="phones",
        storage_gb=storage,
        carrier=carrier or None,
        colorway=colorway or None,
        condition=listing.condition or "good",
    )
