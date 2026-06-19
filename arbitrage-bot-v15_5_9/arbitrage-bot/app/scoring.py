"""Opportunity scoring + risk filters."""
from __future__ import annotations
import re as _re
from datetime import datetime, timezone
from .models import Listing, NormalizedIdentity, Opportunity, CompMatch, _utcnow
from .pricing.profit import ProfitBreakdown

# ── Risk detection ──────────────────────────────────────────────────

# Strong/critical damage signals — match becomes a critical flag that
# kills the score. Mirrors the worst PHONE_COMP_NEGATIVES.
DAMAGE_KEYWORDS = [
    "broken", "cracked", "damaged", "for parts", "as-is", "as is",
    "not working", "won't turn on", "wont turn on", "salvage",
    "jailbroken", "spares or repair", "spares or repairs",
    "for repair", "faulty", "smashed", "no power", "no display",
    "no screen", "screen replacement", "lcd assembly",
    "logic board", "motherboard only", "shell only", "frame only",
    # Locked / blacklisted (phones)
    "icloud locked", "activation locked", "blacklisted",
    "blocked imei", "bad imei",
]

# Soft damage signals — flag the listing as risky but don't critical-kill.
# These mean "there's a problem, read the description carefully" but the
# item could still be a legitimate flip if you understand what you're buying.
SOFT_DAMAGE_KEYWORDS = [
    "damage", "with damage", "has damage", "any damage", "minor damage",
    "cosmetic damage", "cracked back", "back glass damage", "dent",
    "scratched screen", "scratch on screen",
    "reparable", "repairable",
    "read description", "read desc",
    "as described", "see photos", "see pics",
]

COUNTERFEIT_KEYWORDS = ["replica", "rep", "1:1", "unauthorized", "ua",
                        "bootleg", "fake"]
LOT_KEYWORDS = ["lot of", "bundle", "wholesale", "pallet"]

ACCESSORY_KEYWORDS = [
    " case ", " case,", " cases ", "phone case", "laptop case",
    "cover for", "protective cover",
    "screen protector", "tempered glass", "glass film",
    "charging cable", "usb cable", "lightning cable",
    "charger for", "charger compatible", "wall charger", "car charger",
    "adapter for", "power adapter",
    "stand for", "phone stand", "laptop stand",
    "mount for", "car mount", "holder for", "phone holder",
    "skin for", "decal for", "sticker for", "wrap for",
    "pouch for", "sleeve for", "strap for", "wrist strap",
    "dock for", "docking station", "hub for", "usb hub",
    "dongle", "stylus for", "keyboard cover", "trackpad cover",
    "earbuds", "earphones", "headphones",
    "wallet case", "card holder for", "grip for", "ring holder",
    "replacement battery", "replacement screen", "lcd assembly",
    "repair kit", "tool kit", "sim tray", "sim card tray",
    "laces for", "insole", "shoe tree", "shoe box only", "sole protector",
    "box only", "empty box", "packaging only",   # v15.4.6
    "poster", "keyring", "keychain", "miniature", "model toy",  # v15.4.6
]

PRICE_FLOOR = {"shoes": 35.0, "phones": 70.0, "laptops": 130.0}


def _matches_keyword_word_boundary(title_l: str, keywords: list[str]) -> str | None:
    """
    Match a list of keywords against the title using word boundaries
    (so "broken" matches "(Unlocked)Broken" but not "casey").
    Returns first matched keyword, or None.
    """
    for kw in keywords:
        if " " in kw or "-" in kw or "'" in kw:
            # Multi-token — substring match
            if kw in title_l:
                return kw
        else:
            # Single token — word boundary
            if _re.search(rf"\b{_re.escape(kw)}\b", title_l):
                return kw
    return None


def detect_risk_flags(listing: Listing, identity: NormalizedIdentity) -> list[str]:
    flags: list[str] = []
    title_l = listing.title.lower()
    title_padded = f" {title_l} "

    # Multi-variant candidate (v15.4.8) — listing claims to be one of
    # several storage / colour variants. Can't be valued because we don't
    # know which variant the price applies to.
    from .pricing.comps import _is_multi_variant
    if _is_multi_variant(listing.title):
        flags.append("multi_variant_candidate")

    # Suspicious seller patterns (v15.4.8) — listings claiming "NEW" / "BOXED"
    # for refurbished or aftermarket housing. These price 2-3x above the
    # genuine used market and should not be treated as flippable.
    SUSPICIOUS_SELLER_PATTERNS = [
        "apple replacement",     # third-party refurb sold as new
        "aftermarket",
        "non genuine",
        "non-genuine",
        "replacement housing",
        "replacement screen",
        "rebuilt",
        "refurbished housing",
    ]
    if any(p in title_l for p in SUSPICIOUS_SELLER_PATTERNS):
        flags.append("suspicious_new_claim")

    # Strong damage — critical flag, will kill the score
    if _matches_keyword_word_boundary(title_l, DAMAGE_KEYWORDS):
        flags.append("damaged_or_parts")

    # Soft damage — flag for visibility, NOT critical
    if _matches_keyword_word_boundary(title_l, SOFT_DAMAGE_KEYWORDS):
        flags.append("possible_damage")

    if _matches_keyword_word_boundary(title_l, COUNTERFEIT_KEYWORDS):
        flags.append("possible_counterfeit")
    if any(k in title_l for k in LOT_KEYWORDS):
        flags.append("bundle_or_lot")
    if any(k in title_padded for k in ACCESSORY_KEYWORDS):
        flags.append("accessory_not_product")
    if listing.is_auction:
        flags.append("auction")
    if listing.pickup_only:
        flags.append("pickup_only")
    if listing.seller_rating is not None and listing.seller_rating < 0.90:
        flags.append("low_seller_rating")

    floor = PRICE_FLOOR.get(listing.category, 0)
    if floor and listing.price < floor:
        flags.append("below_price_floor")

    if listing.category == "phones":
        carrier = (identity.carrier or "").lower()
        is_locked = carrier and carrier != "unlocked" and "unlocked" not in carrier
        if is_locked or "icloud" in title_l:
            flags.append("locked_phone")

        # Battery health rules (v15.4.5):
        # 1. If condition isn't new/like_new and title doesn't mention battery
        #    health at all, flag missing_battery_health (informational only).
        # 2. If title states battery health below 90%, flag low_battery_health.
        #    These listings sell at a discount precisely because the battery
        #    is degraded — using them as comps without flagging skews comps
        #    low, and buying them yields a phone harder to resell at full
        #    market price.
        if listing.condition not in ("new", "like_new"):
            has_battery_info = any(k in title_l for k in [
                "battery health", "battery cap", "battery 100",
                "battery 9", "battery 8", "% battery",
                "100% bh", "100% battery",
            ])
            if not has_battery_info:
                flags.append("missing_battery_health")

            # Look for an explicit percentage like "87%" or "87 %" or "87% bh"
            import re as _re
            bh_match = _re.search(
                r"\b(\d{1,3})\s*%\s*(?:bh|battery|battery\s+health)?\b",
                title_l,
            )
            if bh_match:
                try:
                    bh = int(bh_match.group(1))
                    if 50 <= bh < 90:
                        flags.append("low_battery_health")
                except ValueError:
                    pass
    if listing.category == "laptops":
        if identity.charger_included is False:
            flags.append("no_charger")
    if listing.category == "shoes":
        if not identity.size:
            flags.append("missing_size")

    return flags


CRITICAL_FLAGS = {
    "damaged_or_parts", "possible_counterfeit", "locked_phone",
    "accessory_not_product", "below_price_floor",
    # v15.4.8
    "multi_variant_candidate",   # can't value a multi-storage/colour listing
    "suspicious_new_claim",       # "NEW" Apple Replacement / aftermarket
}

WEIGHTS = dict(profit=0.25, roi=0.15, confidence=0.20,
               liquidity=0.10, match_quality=0.15, risk=0.10, staleness=0.05)


def score_opportunity(
    listing: Listing,
    identity: NormalizedIdentity,
    comp: CompMatch,
    profit: ProfitBreakdown,
) -> Opportunity:
    flags = detect_risk_flags(listing, identity)

    norm_profit = max(0.0, min(profit.net_profit / 200.0, 1.0))
    norm_roi = max(0.0, min(profit.roi, 1.0))
    risk_penalty = min(1.0, len(flags) / 4.0)

    hours_old = max(0.0, (_utcnow() - listing.scraped_at).total_seconds() / 3600)
    staleness = min(hours_old / 72.0, 1.0)

    score = (
        WEIGHTS["profit"] * norm_profit
        + WEIGHTS["roi"] * norm_roi
        + WEIGHTS["confidence"] * comp.confidence
        + WEIGHTS["liquidity"] * comp.liquidity
        + WEIGHTS["match_quality"] * comp.match_quality
        - WEIGHTS["risk"] * risk_penalty
        - WEIGHTS["staleness"] * staleness
    )

    if any(f in CRITICAL_FLAGS for f in flags):
        score = min(score, 0.0)

    return Opportunity(
        listing=listing,
        identity=identity,
        fair_value=comp.fair_value,
        expected_resale=comp.expected_resale,
        fees=profit.resale_fee + profit.payment_fee,
        net_profit=profit.net_profit,
        roi=profit.roi,
        confidence=comp.confidence,
        liquidity=comp.liquidity,
        risk_flags=flags,
        score=round(score, 4),
        comp_source=comp.source,
        comp_count=comp.sample_size,
        match_quality=comp.match_quality,
        match_details=comp.match_details,
        comp_evidence=comp.comp_evidence,
    )
