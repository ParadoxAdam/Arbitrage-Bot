"""
Manual reference anchors for common UK unlocked iPhones.

These are sanity-check ranges representing normal **working-condition,
unlocked, healthy-battery** devices on eBay UK as of v15.5 release.
They are not authoritative prices — only stabilisers used by the
valuation engine to flag suspicious comp-based estimates and widen
confidence ranges.

Anchors do NOT create candidates by themselves. The engine never
inflates a comp-based valuation up toward the anchor; it only:
  - flags `valuation_suspicious_low` when comps cluster far below
  - flags `valuation_suspicious_high` when comps cluster far above
  - widens conservative/optimistic bounds when comp spread is wide
  - caps confidence when comps and anchors disagree

Update these manually as the market shifts. Each anchor records when
it was last reviewed and the source label so future calibration is auditable.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceAnchor:
    category: str
    brand: str
    model: str                  # canonical normalized model, e.g. "iphone 14 pro"
    storage_gb: int | None
    carrier: str                # "unlocked" or carrier name
    low: float
    mid: float
    high: float
    notes: str
    last_updated: str           # ISO date
    source_label: str           # human-readable origin


# UK unlocked iPhone anchors (v15.5 baseline — review quarterly)
# Ranges represent active-listing market clearing prices for healthy
# devices, NOT sold-listing medians.
_ANCHORS: list[ReferenceAnchor] = [
    # ── v15.5.7 recalibration ─────────────────────────────────────
    # Anchors recalibrated against Adam's Apr 28 DB sample of UK active
    # listings. The previous v15.5 anchors were 10-39% too high — every
    # iPhone Pro/Pro Max valuation was being tagged anchor_driven_review_only
    # because of comp/anchor disagreement.
    #
    # New convention: mid = observed active asking median; low/high are
    # ±15% around it. This makes the anchor a sanity check on comp-pool
    # cleanliness rather than a separate market opinion.
    # Sample sizes from Apr 28 dump shown in parentheses.

    # iPhone 13 Pro (3 samples in dump; 256GB observed median £298)
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 13 pro",
        storage_gb=128, carrier="unlocked",
        low=235, mid=275, high=320,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 13 pro",
        storage_gb=256, carrier="unlocked",
        low=255, mid=298, high=345,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    # iPhone 13 Pro Max (256GB observed median £350)
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 13 pro max",
        storage_gb=128, carrier="unlocked",
        low=275, mid=320, high=370,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 13 pro max",
        storage_gb=256, carrier="unlocked",
        low=300, mid=350, high=405,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    # iPhone 14 Pro (observed median £349 across mixed storage)
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 14 pro",
        storage_gb=128, carrier="unlocked",
        low=290, mid=340, high=390,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 14 pro",
        storage_gb=256, carrier="unlocked",
        low=320, mid=375, high=430,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    # iPhone 14 Pro Max (observed median £391)
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 14 pro max",
        storage_gb=128, carrier="unlocked",
        low=335, mid=390, high=450,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 14 pro max",
        storage_gb=256, carrier="unlocked",
        low=370, mid=435, high=500,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    # iPhone 15 Pro (observed median £450)
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 15 pro",
        storage_gb=128, carrier="unlocked",
        low=385, mid=450, high=520,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 15 pro",
        storage_gb=256, carrier="unlocked",
        low=420, mid=495, high=570,
        notes="Active asking median ±15%",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
    # iPhone 15 Pro Max (23 samples, observed median £496.30)
    ReferenceAnchor(
        category="phones", brand="Apple", model="iphone 15 pro max",
        storage_gb=256, carrier="unlocked",
        low=420, mid=496, high=570,
        notes="Active asking median ±15% (n=23)",
        last_updated="2026-04-28", source_label="UK eBay survey v15.5.7",
    ),
]


def find_anchor(
    category: str,
    brand: str,
    model: str,
    storage_gb: int | None,
    carrier: str | None = None,
) -> ReferenceAnchor | None:
    """
    Look up the matching anchor, or None if no match exists.

    v15.5.2: carrier handling is now explicit:
      - carrier="unlocked" or contains "unlocked" → returns unlocked anchor
      - carrier="" or None  → returns None (caller decides whether to use a
                              loose/unknown-carrier anchor with a warning)
      - carrier matches a specific locked carrier in the anchor table
                              (none currently exist) → returns that

    To intentionally treat an unknown-carrier listing as if it were unlocked
    (with reduced confidence), use `find_anchor_loose` instead.
    """
    if not category or not brand or not model:
        return None
    cat_l = category.lower().strip()
    brand_l = brand.lower().strip()
    model_l = model.lower().strip()

    # v15.5.2: do NOT silently default empty carrier to "unlocked"
    if not carrier:
        return None
    carrier_l = carrier.lower().strip()
    if not carrier_l:
        return None

    for a in _ANCHORS:
        if a.category != cat_l:
            continue
        if a.brand.lower() != brand_l:
            continue
        if a.model != model_l:
            continue
        if storage_gb is not None and a.storage_gb != storage_gb:
            continue
        if a.carrier.lower() != carrier_l:
            continue
        return a
    return None


def find_anchor_loose(
    category: str,
    brand: str,
    model: str,
    storage_gb: int | None,
) -> ReferenceAnchor | None:
    """
    Find an anchor when the listing's carrier status is unknown.

    Returns the unlocked anchor for the model if one exists, but the caller
    must (a) lower confidence, (b) reduce the anchor's blend weight, and
    (c) attach a `carrier_unknown_anchor_weak` warning to the valuation.

    Used by the valuation engine when identity.carrier is empty/unknown.
    """
    return find_anchor(category, brand, model, storage_gb, carrier="unlocked")


def all_anchors() -> list[ReferenceAnchor]:
    """Return a copy of all configured anchors (for tests/inspection)."""
    return list(_ANCHORS)
