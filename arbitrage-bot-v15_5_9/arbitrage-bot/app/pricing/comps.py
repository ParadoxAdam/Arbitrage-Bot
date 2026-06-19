"""
Spec-aware comp engine with TIERED matching.

Three tiers:
  EXACT   — same product, same critical specs. High confidence.
  PARTIAL — same product family, slight spec drift. Used for review context only.
  BROAD   — wrong chip generation, different model, etc. Rejected.

For phones: storage_gb is critical; carrier matters. Different generation = broad.
For laptops: chip family + RAM + storage are all critical. Year is a tiebreaker.
For shoes: SKU is gold; size match is required for exact, nearby size = partial.

The candidate's own listing (matched by source_item_id) is ALWAYS excluded
from its own comp set.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Optional
from ..config import settings
from ..models import Listing, NormalizedIdentity, CompMatch

log = logging.getLogger("comps")


# ── Match tiers ─────────────────────────────────────────────────────

TIER_EXACT = "exact"
TIER_PARTIAL = "partial"
TIER_BROAD = "broad"


@dataclass
class CompCandidate:
    """A potential comp with its tier classification."""
    price: float
    title: str
    source_item_id: str
    tier: str                  # exact | partial | broad
    reason: str                # human-readable explanation


@dataclass
class CompResolution:
    """Outcome of running the comp engine for one listing."""
    exact: list[CompCandidate] = field(default_factory=list)
    partial: list[CompCandidate] = field(default_factory=list)
    broad: list[CompCandidate] = field(default_factory=list)
    excluded_self: int = 0
    reasons: dict[str, int] = field(default_factory=dict)  # rejection reason -> count

    # v15.3: hygiene diagnostics propagated from the fetch layer
    raw_fetched: int = 0
    dropped_negative: int = 0
    dropped_price_floor: int = 0
    dropped_no_product_token: int = 0
    pool_rejected: bool = False
    pool_rejection_reason: str = ""

    def add_reason(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


# ═══════════════════════════════════════════════════════════════════
# Mock comp table — backed by sold-data tier
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CompEntry:
    prices: list[float]
    source: str
    spec: dict = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)


COMP_TABLE: dict[str, CompEntry] = {
    "shoes|nike|air jordan 1 chicago 2022": CompEntry(
        prices=[340, 355, 325, 365, 335, 350, 360, 330],
        source="sold",
        spec={"size": "10", "condition": "new"},
        titles=[f"Jordan 1 Chicago 2022 size 10 (comp #{i})" for i in range(8)],
    ),
    "phones|apple|iphone 14 pro|256": CompEntry(
        prices=[620, 630, 600, 635, 615, 625, 610],
        source="sold",
        spec={"storage_gb": 256, "carrier": "unlocked"},
        titles=[f"iPhone 14 Pro 256GB unlocked (comp #{i})" for i in range(7)],
    ),
    "laptops|apple|macbook pro 14|m2 pro|16|512": CompEntry(
        prices=[1150, 1190, 1175, 1205, 1160, 1185, 1195],
        source="sold",
        spec={"ram_gb": 16, "storage_gb": 512, "cpu": "M2 Pro"},
        titles=[f"MacBook Pro 14 M2 Pro 16/512 (comp #{i})" for i in range(7)],
    ),
}

EBAY_CATEGORIES = {
    "shoes": "93427", "phones": "9355", "laptops": "177",
}

ACTIVE_LISTING_DISCOUNT = {
    "shoes": 0.82, "phones": 0.88, "laptops": 0.87, "other": 0.85,
}

# Absolute condition values (relative to "new" = 1.0).
# Used to compute the *delta* between target and comp-pool conditions —
# never applied directly as a fixed discount on resale.
CONDITION_VALUE = {
    "new": 1.00,
    "like_new": 0.93,
    "good": 0.85,
    "fair": 0.72,
    "parts": 0.30,
}

# Backwards-compat alias kept so older tests/code don't break.
CONDITION_DISCOUNT = CONDITION_VALUE


# ── Multi-variant comp detection (v15.4.7) ──────────────────────────
# Phrases that indicate the listing is a multi-variant placeholder
# priced at the cheapest variant — these are not exact comps.
MULTI_VARIANT_TOKENS = [
    "all colours", "all colors", "all sizes",
    "choose colour", "choose color", "choose storage",
    "all variants", "any colour", "any color",
    "any size", "all options",
    "from £", "from $", "starting at",
]

# Storage patterns indicating multi-storage listings (e.g. "128/256/512GB"
# or "128GB/256GB/512GB" or "128-512gb"). These price at the cheapest tier.
import re as _re_v_15_4_7
_MULTI_STORAGE = _re_v_15_4_7.compile(
    # 128/256/512 or 128GB/256GB/512GB or 128-512gb or 128,256,512 etc
    r"\d{2,4}\s*(?:gb)?\s*[/,&\-]\s*\d{2,4}\s*(?:gb)?(?:\s*[/,&\-]\s*\d{2,4}\s*(?:gb)?)?",
    _re_v_15_4_7.IGNORECASE,
)


def _is_multi_variant(title: str) -> bool:
    """True if the title looks like a multi-variant listing whose price
    represents only the cheapest variant."""
    title_l = title.lower()
    if any(tok in title_l for tok in MULTI_VARIANT_TOKENS):
        return True
    if _MULTI_STORAGE.search(title_l):
        return True
    return False


def _detect_comp_condition(title: str) -> str:
    """
    Best-effort condition extraction from a comp listing title.

    Returns one of: new | like_new | good | fair | parts | unknown
    Used to weight comp prices by their declared condition.
    """
    t = title.lower()
    # Order matters — check most specific first
    if any(k in t for k in [
        "for parts", "parts only", "spares or repair", "for repair",
        "faulty", "broken", "cracked", "smashed", "damage",
    ]):
        return "parts"
    if any(k in t for k in [
        "fair condition", "below average", "heavy wear",
        "scratched screen", "deep scratch",
    ]):
        return "fair"
    if any(k in t for k in [
        "very good", "excellent", "pristine", "mint",
        "near mint", "as new", "like new", "open box",
        "refurbished", "refurb",
    ]):
        return "like_new"
    if "brand new" in t or " new " in f" {t} " or "sealed" in t \
            or "new condition" in t:
        return "new"
    if "good condition" in t or "good cond" in t or "tested working" in t:
        return "good"
    return "unknown"


def _avg_comp_condition_value(titles: list[str]) -> tuple[float, dict]:
    """
    Estimate the average condition value of the comp pool.

    Returns (avg_value_in_0_to_1, breakdown_dict) where breakdown shows
    how many comps had each condition.

    If most comps are unknown condition, return 0.85 (used/good baseline)
    so we don't either over-inflate or under-discount.
    """
    breakdown = {"new": 0, "like_new": 0, "good": 0, "fair": 0,
                 "parts": 0, "unknown": 0}
    values = []
    for t in titles:
        c = _detect_comp_condition(t)
        breakdown[c] += 1
        if c != "unknown":
            values.append(CONDITION_VALUE[c])

    if values:
        avg = sum(values) / len(values)
    else:
        # No condition info anywhere — assume "good" as the eBay baseline
        # for active used-phone listings.
        avg = CONDITION_VALUE["good"]

    return avg, breakdown


# ═══════════════════════════════════════════════════════════════════
# Tier classification
# ═══════════════════════════════════════════════════════════════════

def _normalize_chip(chip: str | None) -> str:
    """Normalize chip strings for comparison: 'M2 Pro' / 'm2 pro' / 'M2pro' all equal."""
    if not chip:
        return ""
    c = chip.lower().replace("-", " ").strip()
    c = " ".join(c.split())
    return c


def _chip_family(chip: str | None) -> str:
    """Return the generation+tier (e.g. 'm2 pro' from 'M2 Pro 12-core')."""
    norm = _normalize_chip(chip)
    if not norm:
        return ""
    # Extract first M-token + optional Pro/Max/Ultra
    import re
    m = re.match(r'(m\d{1,2})(?:\s+(pro|max|ultra))?', norm)
    if m:
        return f"{m.group(1)} {m.group(2)}".strip() if m.group(2) else m.group(1)
    return norm


def _tier_for_shoe(item_spec: dict, identity: NormalizedIdentity
                   ) -> tuple[str, str]:
    """Tier a shoe comp. Returns (tier, reason)."""
    # SKU exact match wins regardless of size
    if identity.sku and item_spec.get("sku"):
        if identity.sku.lower() == item_spec["sku"].lower():
            return TIER_EXACT, "SKU exact match"
        return TIER_BROAD, f"different SKU ({item_spec['sku']} vs {identity.sku})"

    # Size — strongly required
    if identity.size and item_spec.get("size"):
        try:
            t = float(identity.size)
            c = float(item_spec["size"])
            if abs(t - c) < 0.001:
                # Same size — consider colorway
                if identity.colorway and item_spec.get("colorway"):
                    if identity.colorway.lower() == item_spec["colorway"].lower():
                        return TIER_EXACT, f"size + colorway match (size {identity.size})"
                    return TIER_PARTIAL, f"size match, different colorway"
                return TIER_EXACT, f"size match (size {identity.size})"
            elif abs(t - c) <= 0.5:
                return TIER_PARTIAL, f"nearby size ({item_spec['size']} vs {identity.size})"
            else:
                return TIER_BROAD, f"size mismatch ({item_spec['size']} vs {identity.size})"
        except ValueError:
            pass

    # Missing size on either side
    if not identity.size:
        return TIER_PARTIAL, "listing missing size"
    if not item_spec.get("size"):
        return TIER_PARTIAL, "comp missing size"

    return TIER_BROAD, "insufficient info to match"


def _tier_for_phone(item_spec: dict, identity: NormalizedIdentity,
                    item_title: str = "", item_model: str = ""
                    ) -> tuple[str, str]:
    """
    Tier a phone comp.

    v15.1 rules:
    - Family match required for EXACT or PARTIAL: iPhone 14 Pro and
      iPhone 14 Pro Max are different products.
    - Storage match required for EXACT.
    - Carrier match required for EXACT *if both have known carrier*.
      If candidate is unlocked but comp has unknown carrier → PARTIAL,
      not exact (can't assume an unknown comp is also unlocked).
    v15.4.7:
    - Multi-variant titles (All Colours / 128/256/512GB / Choose Storage)
      can never be EXACT — their price reflects only the cheapest variant.
    """
    # ── Multi-variant check (v15.4.7) ───────────────────────────
    # A multi-storage / multi-color listing is at best a PARTIAL comp,
    # because its price corresponds to one specific (usually cheapest)
    # variant we can't identify from the title.
    is_multi = _is_multi_variant(item_title)

    # ── Family check (variant + generation) ─────────────────────
    target_text = " ".join([identity.model or "", identity.colorway or ""])
    comp_text = " ".join([item_model or "", item_title or ""])
    fam_match, fam_reason = _phone_families_match(target_text, comp_text)
    if not fam_match:
        return TIER_BROAD, fam_reason

    # ── Storage match ───────────────────────────────────────────
    if identity.storage_gb and item_spec.get("storage_gb"):
        if identity.storage_gb != item_spec["storage_gb"]:
            return TIER_BROAD, (
                f"wrong storage "
                f"({item_spec['storage_gb']}GB vs {identity.storage_gb}GB)"
            )
    elif not identity.storage_gb:
        return TIER_PARTIAL, "listing missing storage"
    elif not item_spec.get("storage_gb"):
        return TIER_PARTIAL, "comp missing storage"

    # ── Carrier match ───────────────────────────────────────────
    # If candidate carrier is known AND comp carrier is known → check
    # If candidate is unlocked but comp carrier is unknown → PARTIAL
    target_carrier = (identity.carrier or "").lower().strip()
    comp_carrier = (item_spec.get("carrier") or "").lower().strip()

    # A successful tier decision; downgrade to PARTIAL if multi-variant.
    def _maybe_downgrade(tier, reason):
        if tier == TIER_EXACT and is_multi:
            return TIER_PARTIAL, reason + " (multi-variant listing)"
        return tier, reason

    if target_carrier and comp_carrier:
        # Both known
        target_unlocked = "unlocked" in target_carrier
        comp_unlocked = "unlocked" in comp_carrier
        if target_unlocked and comp_unlocked:
            return _maybe_downgrade(TIER_EXACT, "family + storage + unlocked match")
        if target_unlocked != comp_unlocked:
            return TIER_PARTIAL, "carrier lock differs"
        if target_carrier == comp_carrier:
            return _maybe_downgrade(TIER_EXACT, "family + storage + same carrier")
        return TIER_PARTIAL, "different carrier"

    if target_carrier and not comp_carrier:
        # We're unlocked, comp is unknown — can't assume same
        if "unlocked" in target_carrier:
            return TIER_PARTIAL, "candidate unlocked, comp carrier unknown"
        return TIER_PARTIAL, "carrier info missing on comp"

    if not target_carrier and comp_carrier:
        return TIER_PARTIAL, "carrier info missing on candidate"

    # Both unknown
    return TIER_PARTIAL, "carrier info unavailable"


def _tier_for_laptop(item_spec: dict, identity: NormalizedIdentity,
                     item_title: str = "", item_model: str = ""
                     ) -> tuple[str, str]:
    """
    Tier a laptop comp.
    For MacBooks: family must match (Air vs Pro, 13 vs 14 vs 16).
    For Apple silicon: chip family must match for EXACT.
    """
    # Family check for MacBooks
    target_text = (identity.model or "")
    comp_text = " ".join([item_model or "", item_title or ""])

    if "macbook" in target_text.lower():
        fam_match, fam_reason = _macbook_families_match(target_text, comp_text)
        if not fam_match:
            return TIER_BROAD, fam_reason

    # Compare chip family
    target_chip = _chip_family(identity.cpu)
    comp_chip = _chip_family(item_spec.get("cpu"))

    if target_chip and comp_chip:
        if target_chip != comp_chip:
            tg = target_chip.split()[0]
            cg = comp_chip.split()[0]
            if tg == cg:
                # Same generation, different tier — partial
                pass
            else:
                return TIER_BROAD, (f"wrong chip generation "
                                    f"({item_spec.get('cpu')} vs {identity.cpu})")

    if identity.ram_gb and item_spec.get("ram_gb"):
        if identity.ram_gb != item_spec["ram_gb"]:
            return TIER_BROAD, (f"wrong RAM "
                                f"({item_spec['ram_gb']}GB vs {identity.ram_gb}GB)")

    if identity.storage_gb and item_spec.get("storage_gb"):
        if identity.storage_gb != item_spec["storage_gb"]:
            return TIER_BROAD, (f"wrong storage "
                                f"({item_spec['storage_gb']}GB vs {identity.storage_gb}GB)")

    matches = []
    if target_chip and comp_chip:
        if target_chip == comp_chip:
            matches.append("chip exact")
        else:
            matches.append("same chip family, different tier")
    if identity.ram_gb and item_spec.get("ram_gb") and \
            identity.ram_gb == item_spec["ram_gb"]:
        matches.append(f"RAM ({identity.ram_gb}GB)")
    if identity.storage_gb and item_spec.get("storage_gb") and \
            identity.storage_gb == item_spec["storage_gb"]:
        matches.append(f"storage ({identity.storage_gb}GB)")

    if not matches:
        return TIER_PARTIAL, "insufficient spec overlap"

    has_chip_match = target_chip and comp_chip and target_chip == comp_chip
    has_full_specs = bool(identity.ram_gb and identity.storage_gb)

    if has_chip_match and has_full_specs:
        return TIER_EXACT, "; ".join(matches)
    return TIER_PARTIAL, "; ".join(matches) + " (partial overlap)"


# ═══════════════════════════════════════════════════════════════════
# Model family checks (v15.1)
# ═══════════════════════════════════════════════════════════════════

import re as _re

# Extract canonical iPhone family: "iphone 14 pro", "iphone 14 pro max", etc.
_IPHONE_PATTERN = _re.compile(
    r"iphone\s+(\d{1,2})\s*(pro\s*max|pro|plus|mini)?",
    _re.IGNORECASE,
)

# Other phone families
_PHONE_FAMILY_PATTERNS = [
    _re.compile(r"galaxy\s+s\d{1,2}\s*(ultra|plus|fe)?", _re.IGNORECASE),
    _re.compile(r"galaxy\s+note\s*\d{1,2}", _re.IGNORECASE),
    _re.compile(r"galaxy\s+z\s+(flip|fold)\s*\d?", _re.IGNORECASE),
    _re.compile(r"pixel\s+\d\s*(pro|xl|a)?", _re.IGNORECASE),
]

# Laptop families
_MACBOOK_FAMILY = _re.compile(
    r"macbook\s+(pro|air)\s+(\d{2})", _re.IGNORECASE,
)


def _phone_family(text: str) -> str:
    """
    Extract canonical phone family from a model/title string.
    Returns e.g. 'iphone 14 pro max' or 'iphone 14 pro' — these MUST differ
    for an iPhone 14 Pro Max to be rejected as a comp for an iPhone 14 Pro.
    """
    if not text:
        return ""
    m = _IPHONE_PATTERN.search(text)
    if m:
        gen = m.group(1)
        variant = (m.group(2) or "").lower().strip()
        # Normalize "pro max" / "promax" / etc.
        variant = " ".join(variant.split())
        return f"iphone {gen} {variant}".strip()
    for p in _PHONE_FAMILY_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(0).lower().strip()
    return ""


def _macbook_family(text: str) -> str:
    """Extract 'macbook pro 14' / 'macbook air 13' etc."""
    if not text:
        return ""
    m = _MACBOOK_FAMILY.search(text)
    if m:
        family = m.group(1).lower()
        size = m.group(2)
        return f"macbook {family} {size}"
    return ""


def _phone_families_match(target_text: str, comp_text: str) -> tuple[bool, str]:
    """
    Returns (match, reason).
    iPhone 14 Pro vs iPhone 14 Pro Max → mismatch (different variant)
    iPhone 14 Pro vs iPhone 15 Pro → mismatch (different generation)
    """
    tf = _phone_family(target_text)
    cf = _phone_family(comp_text)
    if not tf or not cf:
        return False, "could not extract phone family"
    if tf == cf:
        return True, f"family match ({tf})"
    return False, f"different family ({cf} vs {tf})"


def _macbook_families_match(target_text: str, comp_text: str) -> tuple[bool, str]:
    tf = _macbook_family(target_text)
    cf = _macbook_family(comp_text)
    if not tf or not cf:
        return False, "could not extract macbook family"
    if tf == cf:
        return True, f"family match ({tf})"
    return False, f"different family ({cf} vs {tf})"


def _classify(item_spec: dict, item_title: str,
              identity: NormalizedIdentity,
              item_brand: str = "", item_model: str = "") -> tuple[str, str]:
    """
    Dispatch to category-specific classifier.

    item_brand and item_model are the comp's normalized brand/model
    (used for model-family validation in v15.1).
    """
    # Brand mismatch is an automatic broad reject across categories
    if identity.brand and item_brand:
        if identity.brand.lower().strip() != item_brand.lower().strip():
            return TIER_BROAD, (
                f"different brand ({item_brand} vs {identity.brand})"
            )

    if identity.category == "shoes":
        return _tier_for_shoe(item_spec, identity)
    if identity.category == "phones":
        return _tier_for_phone(item_spec, identity, item_title, item_model)
    if identity.category == "laptops":
        return _tier_for_laptop(item_spec, identity, item_title, item_model)
    return TIER_PARTIAL, "no category rules"


# ═══════════════════════════════════════════════════════════════════
# Outlier removal
# ═══════════════════════════════════════════════════════════════════

def _remove_outliers(prices: list[float]) -> list[float]:
    if len(prices) < 4:
        return prices
    s = sorted(prices)
    n = len(s)
    q1, q3 = s[n // 4], s[3 * n // 4]
    iqr = q3 - q1
    if iqr == 0:
        return prices
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    cleaned = [p for p in s if lower <= p <= upper]
    return cleaned if len(cleaned) >= 3 else prices


# ═══════════════════════════════════════════════════════════════════
# Confidence with hard caps
# ═══════════════════════════════════════════════════════════════════

def _calc_confidence(
    prices: list[float], med: float, is_sold: bool,
    match_quality: float, sample_size: int,
) -> float:
    size_score = min(1.0, len(prices) / 10)

    if med > 0 and len(prices) > 1:
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        cv = (variance ** 0.5) / mean
        spread_score = max(0.0, 1.0 - cv * 3)
    else:
        spread_score = 0.5

    source_score = 0.9 if is_sold else 0.5

    raw = (
        0.25 * size_score
        + 0.20 * spread_score
        + 0.25 * source_score
        + 0.30 * match_quality
    )

    if match_quality < 0.5:
        raw = min(raw, 0.40)
    if not is_sold and match_quality < 0.7:
        raw = min(raw, 0.50)
    if sample_size < 5:
        raw = min(raw, 0.55)
    if not is_sold and sample_size < 8:
        raw = min(raw, 0.45)

    return round(max(0.0, min(1.0, raw)), 3)


# ═══════════════════════════════════════════════════════════════════
# Live eBay comps (returns CompResolution with tiered classification)
# ═══════════════════════════════════════════════════════════════════

MIN_EXACT_COMPS = 3        # below this, no high-confidence valuation
MIN_TOTAL_COMPS = 3


def _fetch_and_classify(
    listing: Listing,
    identity: NormalizedIdentity,
) -> CompResolution:
    """Fetch live comps and bucket each into exact/partial/broad.

    Now uses the v15.3 fetcher which returns a CompFetchResult with
    full hygiene filtering applied. Pool rejections are propagated as
    explicit reasons.
    """
    res = CompResolution()

    if not settings.ebay_client_id:
        res.add_reason("no_ebay_credentials")
        return res

    try:
        from ..pricing.ebay_comps import fetch_comp_items
        cat_id = EBAY_CATEGORIES.get(identity.category, "")
        fetch_result = fetch_comp_items(
            identity.search_query, identity.category, cat_id, limit=40,
        )
    except Exception as e:
        log.warning("eBay comp fetch failed: %s", e)
        res.add_reason("fetch_failed")
        return res

    # Propagate diagnostic counters
    res.raw_fetched = fetch_result.raw_count
    res.dropped_negative = fetch_result.dropped_negative
    res.dropped_price_floor = fetch_result.dropped_price_floor
    res.dropped_no_product_token = fetch_result.dropped_no_product_token

    # Pool rejected by hygiene filter
    if fetch_result.pool_rejected:
        res.pool_rejected = True
        res.pool_rejection_reason = fetch_result.rejection_reason
        res.add_reason(f"pool_rejected: {fetch_result.rejection_reason}")
        log.info("  comp pool REJECTED for %s: %s",
                 identity.comp_key, fetch_result.rejection_reason)
        return res

    if not fetch_result.items:
        res.add_reason("no_comps_returned")
        return res

    target_id = listing.source_item_id

    for item in fetch_result.items:
        if target_id and item.source_item_id == target_id:
            res.excluded_self += 1
            res.add_reason("excluded_target_listing")
            continue

        tier, reason = _classify(
            item.spec, item.title, identity,
            item_brand=item.brand, item_model=item.model,
        )
        cc = CompCandidate(
            price=item.price, title=item.title,
            source_item_id=item.source_item_id,
            tier=tier, reason=reason,
        )
        if tier == TIER_EXACT:
            res.exact.append(cc)
        elif tier == TIER_PARTIAL:
            res.partial.append(cc)
        else:
            res.broad.append(cc)
        res.add_reason(f"{tier}: {reason}")

    log.info("  comps query='%s' -> exact=%d partial=%d broad=%d (excluded_self=%d)",
             identity.search_query, len(res.exact), len(res.partial),
             len(res.broad), res.excluded_self)
    return res


# ═══════════════════════════════════════════════════════════════════
# Main estimation
# ═══════════════════════════════════════════════════════════════════

# Stats accumulator for end-of-run reporting
class CompStats:
    def __init__(self):
        self.scanned = 0
        self.scored = 0
        self.exact_match = 0
        self.partial_match = 0
        self.broad_rejected = 0     # candidates scored but with broad-only comps (rejected)
        self.no_comps = 0
        self.weak_match = 0


# Module-level singleton — pipeline reads/resets per run
_stats = CompStats()


def get_stats() -> CompStats:
    return _stats


def reset_stats() -> None:
    global _stats
    _stats = CompStats()


# ── Near-miss tracking (v15.1) ──────────────────────────────────────
# Records listings that scored but didn't pass review thresholds.
# Surfaced when a scan finds 0 candidates so we can see WHY nothing fired.

class NearMiss:
    __slots__ = ("title", "url", "price", "shipping",
                 "expected_resale", "net_profit",
                 "roi", "score", "confidence", "match_quality",
                 "comp_source", "comp_count", "category", "fail_reason",
                 "is_genuine_near_miss",
                 # v15.5.4 — propagate v1/v2 estimates so the Top Failed
                 # tab can show them just like the Review Queue cards.
                 "v1_expected_resale", "v2_expected_resale",
                 "valuation_method", "valuation_warnings",
                 # v15.5.6 — full valuation context for the Top Failed
                 # collapsible "Valuation breakdown" section.
                 "valuation_confidence", "conservative_resale",
                 "optimistic_resale", "valuation_breakdown",
                 # v15.5.9 — structured failure-reason codes so the
                 # negotiation analyser can bucket failures correctly.
                 # (The free-text `fail_reason` is human-readable only.)
                 "failure_reasons", "risk_flags")

    def __init__(self, **kwargs):
        if "is_genuine_near_miss" not in kwargs:
            kwargs["is_genuine_near_miss"] = False
        if "shipping" not in kwargs:
            kwargs["shipping"] = 0.0
        # Optional fields default to None so callers don't have to set them
        for opt_field in (
            "v1_expected_resale", "v2_expected_resale",
            "valuation_method", "valuation_warnings",
            # v15.5.6
            "valuation_confidence", "conservative_resale",
            "optimistic_resale", "valuation_breakdown",
            # v15.5.9
            "failure_reasons", "risk_flags",
        ):
            if opt_field not in kwargs:
                kwargs[opt_field] = None
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


_near_misses: list[NearMiss] = []


def add_near_miss(nm: NearMiss) -> None:
    """
    Append a near-miss, deduping by source URL.

    v15.5.5: a single eBay listing scored across multiple scan cycles
    (genuine near-misses get rescored every scan) used to produce duplicate
    rows on the Top Failed tab. Now we drop any earlier entry with the
    same URL before adding the new one — the latest scan wins.
    """
    if nm.url:
        # Drop any prior entry for this URL so the most recent wins
        global _near_misses
        _near_misses = [n for n in _near_misses if n.url != nm.url]
    _near_misses.append(nm)


def get_near_misses(limit: int = 20) -> list[NearMiss]:
    """Return top near-misses, sorted by score descending."""
    return sorted(_near_misses, key=lambda n: n.score, reverse=True)[:limit]


def reset_near_misses() -> None:
    global _near_misses
    _near_misses = []


def estimate(
    listing: Listing, identity: NormalizedIdentity,
) -> Optional[CompMatch]:
    """
    Estimate fair value with tiered comp matching.

    Returns None if there aren't enough EXACT comps and PARTIAL fallback
    isn't useful enough to score with.
    """
    _stats.scanned += 1

    comp_key = identity.comp_key

    # Mock-comps path (only when explicitly enabled in dev/testing)
    entry: Optional[CompEntry] = None
    if settings.use_mock_comps:
        entry = COMP_TABLE.get(comp_key)
        if entry and len(entry.prices) >= 3:
            # Use mock as a single-tier "exact" set
            tier_match_quality = 0.85
            tier_label = "exact"
            exact_titles = entry.titles
            exact_prices = entry.prices
            partial_count = 0
            broad_count = 0
            excluded_self = 0
            return _build_match_from_set(
                identity, entry, exact_prices, exact_titles,
                tier_match_quality, tier_label,
                partial_count=partial_count,
                broad_count=broad_count,
                excluded_self=excluded_self,
            )

    # Live path with tiered classification
    res = _fetch_and_classify(listing, identity)

    # v15.3: hygiene check — if the comp pool was rejected as untrustworthy,
    # don't compute a valuation. This is what stops the £7 "iPhone 13 Pro" bug.
    if res.pool_rejected:
        log.info(
            "  comp pool rejected for %s — %s",
            identity.comp_key, res.pool_rejection_reason,
        )
        _stats.no_comps += 1
        return None

    # Need at least MIN_EXACT_COMPS exact, OR fall back to partial with caps
    if len(res.exact) >= MIN_EXACT_COMPS:
        _stats.exact_match += 1
        prices = [c.price for c in res.exact]
        titles = [c.title for c in res.exact]
        match_quality = 0.85
        tier_label = "exact"
    elif (len(res.exact) + len(res.partial)) >= MIN_TOTAL_COMPS:
        # Fall back to combining exact + partial as a lower-confidence valuation
        _stats.partial_match += 1
        combined = res.exact + res.partial
        prices = [c.price for c in combined]
        titles = [c.title for c in combined]
        match_quality = 0.55         # capped by partial nature
        tier_label = "partial"
    else:
        # Not enough exact OR partial. Reject for valuation.
        if res.broad and not res.exact and not res.partial:
            _stats.broad_rejected += 1
        elif not res.exact and not res.partial and not res.broad:
            _stats.no_comps += 1
        else:
            _stats.weak_match += 1
        return None

    fake_entry = CompEntry(
        prices=prices, source="active", spec=identity.spec_dict, titles=titles,
    )

    return _build_match_from_set(
        identity, fake_entry, prices, titles,
        match_quality, tier_label,
        partial_count=len(res.partial),
        broad_count=len(res.broad),
        excluded_self=res.excluded_self,
    )


def _build_match_from_set(
    identity: NormalizedIdentity,
    entry: CompEntry,
    prices: list[float],
    titles: list[str],
    match_quality: float,
    tier_label: str,
    *,
    partial_count: int = 0,
    broad_count: int = 0,
    excluded_self: int = 0,
) -> CompMatch:
    """
    Compute the valuation from a chosen set of comp prices.

    v15.4.7: condition is now RELATIVE to the comp pool's average condition,
    not an absolute multiplier. If both target and comps are "good", the
    condition adjustment is 1.0 (no discount). If target is worse than the
    pool, we discount by the ratio.
    """
    cleaned = _remove_outliers(prices)
    raw_median = median(cleaned)
    is_sold = entry.source == "sold"

    # ── Step 1: Active-listing discount (asking → sold drift) ──────
    after_active = raw_median
    active_discount = 1.0
    if not is_sold:
        active_discount = ACTIVE_LISTING_DISCOUNT.get(identity.category, 0.85)
        after_active = raw_median * active_discount

    # ── Step 2: Relative condition adjustment ──────────────────────
    # Compare target's condition to the pool's average condition.
    # Adjustment = target_value / comp_pool_value.
    # Capped at 1.0 (we never inflate above the pool baseline).
    target_cond = identity.condition or "good"
    target_value = CONDITION_VALUE.get(target_cond, CONDITION_VALUE["good"])
    pool_avg_value, cond_breakdown = _avg_comp_condition_value(titles)

    if pool_avg_value > 0:
        cond_adjustment = min(1.0, target_value / pool_avg_value)
    else:
        cond_adjustment = 1.0

    expected_resale = round(after_active * cond_adjustment, 2)
    fair_value = round(expected_resale * 0.74, 2)

    confidence = _calc_confidence(
        cleaned, raw_median, is_sold, match_quality, len(cleaned),
    )
    liquidity = round(max(0.0, min(1.0, len(cleaned) / 15)), 3)

    # ── Evidence + breakdown for the dashboard ─────────────────────
    evidence = []
    for p, t in zip(prices[:5], titles[:5]):
        evidence.append({
            "price": round(p, 2),
            "title": t[:80],
            "condition": _detect_comp_condition(t),
        })

    detail_parts = [f"{tier_label} comps ({len(cleaned)} matched)"]
    if partial_count:
        detail_parts.append(f"+{partial_count} partial avail")
    if broad_count:
        detail_parts.append(f"{broad_count} broad rejected")
    if excluded_self:
        detail_parts.append(f"self-citation excluded")

    # Add the discount breakdown so the dashboard can show its math
    detail_parts.append(
        f"raw_median £{raw_median:.2f} × active {active_discount:.2f} "
        f"× cond {cond_adjustment:.2f} = est £{expected_resale:.2f}"
    )
    cond_summary = ", ".join(
        f"{k}={v}" for k, v in cond_breakdown.items() if v > 0
    )
    detail_parts.append(
        f"target_cond={target_cond}, pool_avg={pool_avg_value:.2f} "
        f"({cond_summary})"
    )

    _stats.scored += 1

    return CompMatch(
        fair_value=fair_value,
        expected_resale=expected_resale,
        confidence=confidence,
        sample_size=len(cleaned),
        liquidity=liquidity,
        source=entry.source,
        match_quality=round(match_quality, 2),
        match_details="; ".join(detail_parts),
        comp_evidence=evidence,
    )
