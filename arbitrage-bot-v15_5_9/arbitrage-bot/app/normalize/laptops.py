"""
Laptop normalizer — extracts brand, model family, year, CPU/chip, RAM, storage, screen.

Key design: the comp_key for laptops includes chip family, RAM, and storage,
so an M1 Pro 16/512 doesn't get bucketed with an M5 24/1TB.
"""
from __future__ import annotations
import re
from ..models import Listing, NormalizedIdentity

KNOWN_BRANDS = ["Apple", "Dell", "Lenovo", "HP", "Asus", "Acer",
                "Microsoft", "Razer", "MSI", "Samsung", "LG", "Framework"]

RAM_PATTERN = re.compile(r'(\d{1,3})\s*(?:gb)\s*(?:ram|memory|ddr)',
                         re.IGNORECASE)
RAM_PATTERN_LOOSE = re.compile(r'\b(\d{1,3})\s*gb\b', re.IGNORECASE)
STORAGE_TB = re.compile(r'(\d)\s*(?:tb|TB)(?:\s*(?:ssd|nvme|storage))?')
STORAGE_GB = re.compile(r'(\d{3,4})\s*(?:gb|GB)\s*(?:ssd|nvme|storage)?')
SCREEN_PATTERN = re.compile(r'(\d{2}(?:\.\d)?)["\s]*(?:inch|in\b|")',
                            re.IGNORECASE)
YEAR_PATTERN = re.compile(r'\b(20\d{2})\b')

# MacBook: extract size separately, chip family separately
MACBOOK_SIZE = re.compile(r'\bmacbook\s+(pro|air)\s+(\d{2})\b', re.IGNORECASE)

# Apple silicon — match anywhere in title, supports M1-M9 and Pro/Max/Ultra
APPLE_CHIP = re.compile(
    r'\bM(\d{1,2})\s*(Pro|Max|Ultra)?\b',
    re.IGNORECASE,
)

# Intel patterns
INTEL_CHIP = re.compile(
    r'\b(?:Intel\s+)?(?:Core\s+)?(i[3579])[-\s]?(\d{4,5}\w*)?',
    re.IGNORECASE,
)

# AMD Ryzen
RYZEN_CHIP = re.compile(r'\b(Ryzen\s*\d)(?:\s*(\d{4})\w*)?', re.IGNORECASE)

# Other models — kept conservative
OTHER_MODEL_PATTERNS = [
    r'(thinkpad \w+ \w*\d*)',
    r'(xps \d{2})',
    r'(surface (?:laptop|pro|book)\s*\d*)',
    r'(rog \w+ \w+)',
    r'(latitude \w+)',
    r'(zenbook \w+)',
]


def _parse_macbook(title: str, aspects: dict) -> dict:
    """
    Parse a MacBook title into structured fields.
    Returns {model, chip_family, year} so comp_key can be specific.
    """
    out = {"model": "", "chip_family": "", "year": ""}

    m = MACBOOK_SIZE.search(title)
    if m:
        family = m.group(1).lower()           # "pro" or "air"
        size = m.group(2)
        out["model"] = f"macbook {family} {size}"

    # Chip family — combines generation + tier (M1, M2 Pro, M3 Max, etc.)
    chip = aspects.get("processor", "") or aspects.get("processor type", "")
    chip_match = APPLE_CHIP.search(chip) if chip else APPLE_CHIP.search(title)
    if chip_match:
        gen = chip_match.group(1)
        tier = (chip_match.group(2) or "").strip().lower()
        out["chip_family"] = f"m{gen} {tier}".strip()

    year_match = YEAR_PATTERN.search(title)
    if year_match:
        out["year"] = year_match.group(1)

    return out


def _parse_ram(title: str, aspects: dict) -> int | None:
    raw = aspects.get("ram size", "")
    if raw:
        m = re.search(r'(\d+)', raw)
        if m:
            val = int(m.group(1))
            if 2 <= val <= 256:
                return val
    m = RAM_PATTERN.search(title)
    if m:
        return int(m.group(1))
    # Loose fallback: first standalone NN GB that's a real RAM size
    for m in RAM_PATTERN_LOOSE.finditer(title):
        val = int(m.group(1))
        if val in (4, 8, 16, 24, 32, 36, 48, 64, 96, 128):
            return val
    return None


def _parse_storage(title: str, aspects: dict) -> int | None:
    raw = aspects.get("ssd capacity", aspects.get("hard drive capacity", ""))
    if raw:
        if "tb" in raw.lower():
            m = re.search(r'(\d+)', raw)
            if m:
                return int(m.group(1)) * 1024
        m = re.search(r'(\d+)', raw)
        if m:
            val = int(m.group(1))
            return val * 1024 if val <= 4 else val
    m = STORAGE_TB.search(title)
    if m:
        return int(m.group(1)) * 1024
    m = STORAGE_GB.search(title)
    if m:
        val = int(m.group(1))
        if val >= 128:
            return val
    return None


def _parse_cpu(title: str, aspects: dict) -> str:
    """Generic CPU string for non-Apple machines."""
    raw = aspects.get("processor", aspects.get("processor type", ""))
    if raw and len(raw) > 2:
        return raw

    m = APPLE_CHIP.search(title)
    if m:
        gen = m.group(1)
        tier = (m.group(2) or "").strip()
        return f"M{gen}{(' ' + tier) if tier else ''}"

    m = INTEL_CHIP.search(title)
    if m:
        prefix = m.group(1)
        suffix = m.group(2) or ""
        return f"{prefix}{('-' + suffix) if suffix else ''}"

    m = RYZEN_CHIP.search(title)
    if m:
        return m.group(0).strip()

    return ""


def _parse_screen(title: str, aspects: dict) -> str:
    raw = aspects.get("screen size", "")
    if raw:
        return raw
    m = SCREEN_PATTERN.search(title)
    if m:
        return m.group(1)
    m = MACBOOK_SIZE.search(title)
    if m:
        return m.group(2)
    return ""


def _parse_charger(title: str) -> bool | None:
    title_l = title.lower()
    if "no charger" in title_l or "without charger" in title_l:
        return False
    if "w/ charger" in title_l or "with charger" in title_l:
        return True
    return None


def _parse_year(title: str) -> str:
    m = YEAR_PATTERN.search(title)
    if m:
        y = int(m.group(1))
        if 2008 <= y <= 2030:
            return str(y)
    return ""


def _parse_other_model(title: str, raw_model: str) -> str:
    """Fallback for non-MacBook laptops."""
    if raw_model and len(raw_model) > 3:
        return raw_model
    title_l = title.lower()
    for p in OTHER_MODEL_PATTERNS:
        m = re.search(p, title_l)
        if m:
            return m.group(1).strip()
    return raw_model


def normalize_laptops(listing: Listing, aspects: dict[str, str]) -> NormalizedIdentity:
    brand = aspects.get("brand", listing.brand or "")
    raw_model = aspects.get("model", listing.model or "")
    title = listing.title

    if not brand:
        for b in KNOWN_BRANDS:
            if b.lower() in title.lower():
                brand = b
                break

    is_mac = brand.lower() == "apple" and "macbook" in title.lower()

    # Build the model and chip identity
    if is_mac:
        mac = _parse_macbook(title, aspects)
        model = mac["model"] or _parse_other_model(title, raw_model) or "macbook"
        cpu = mac["chip_family"] or _parse_cpu(title, aspects)
        year = mac["year"] or _parse_year(title)
    else:
        model = _parse_other_model(title, raw_model)
        cpu = _parse_cpu(title, aspects)
        year = _parse_year(title)

    return NormalizedIdentity(
        brand=brand,
        model=model,
        category="laptops",
        ram_gb=_parse_ram(title, aspects),
        storage_gb=_parse_storage(title, aspects),
        cpu=cpu or None,
        screen_size=_parse_screen(title, aspects) or None,
        charger_included=_parse_charger(title),
        condition=listing.condition or "good",
    )
