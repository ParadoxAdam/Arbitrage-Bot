"""
v15.5.8 — missing_battery_health is a soft signal, not a hard defect.

The v15.5.7 logic treated `missing_battery_health` as equivalent to
`possible_damage` or `low_battery_health`, fully disabling the anchor
floor. But on UK eBay most clean phone listings don't state BH explicitly
in the title — that's not a defect, just a thin title.

Now:
  - Hard defects (possible_damage, low_battery_health, fair/parts condition)
    → anchor_floor_disabled_for_defects warning + cons floor at anchor.low*0.50
  - Soft signals (missing_battery_health alone) → cons floor at anchor.low*0.85
  - No signals → cons floor at anchor.low*0.95
"""
import pytest
from app.valuation import value_listing, find_anchor
from app.models import Listing, NormalizedIdentity, CompMatch, _utcnow


def _listing(title, price=400):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category="phones",
        price=price, shipping=0, condition="good",
        scraped_at=_utcnow(), raw={},
    )


def _identity(model="iphone 14 pro", storage_gb=128):
    return NormalizedIdentity(
        brand="Apple", model=model, category="phones",
        storage_gb=storage_gb, condition="good", carrier="unlocked",
    )


def _comp_match(expected_resale=300):
    prices = [280, 290, 300, 310, 320]
    titles = [f"iPhone 14 Pro 128GB Unlocked Good #{i}" for i in range(len(prices))]
    return CompMatch(
        fair_value=expected_resale * 0.74,
        expected_resale=expected_resale,
        confidence=0.55, sample_size=len(prices),
        liquidity=0.5, source="active", match_quality=0.85,
        match_details="exact",
        comp_evidence=[{"price": p, "title": t}
                       for p, t in zip(prices, titles)],
    )


def test_missing_battery_health_alone_does_not_fire_disabled_warning():
    """Soft signal — anchor floor not fully disabled."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm, risk_flags=["missing_battery_health"])
    assert "anchor_floor_disabled_for_defects" not in v.warnings


def test_low_battery_health_does_fire_disabled_warning():
    """Hard signal — anchor floor disabled."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked 78% BH")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm, risk_flags=["low_battery_health"])
    assert "anchor_floor_disabled_for_defects" in v.warnings


def test_possible_damage_does_fire_disabled_warning():
    l = _listing("Apple iPhone 14 Pro 128GB Read Description")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm, risk_flags=["possible_damage"])
    assert "anchor_floor_disabled_for_defects" in v.warnings


def test_no_flags_uses_strict_floor():
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm, risk_flags=[])
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    # Healthy target — conservative should be near anchor.low * 0.95
    # (subject to range invariant capping it at expected_resale)
    expected_floor = min(anchor.low * 0.95, v.expected_resale)
    assert v.conservative_resale >= expected_floor - 0.5


def test_missing_bh_uses_softer_floor_than_strict():
    """missing_battery_health gives a softer floor than no flags would."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    # Force comp_match estimate well below strict anchor floor
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    cm = _comp_match(expected_resale=anchor.low * 0.6)
    v_clean = value_listing(l, i, cm, risk_flags=[])
    v_missing = value_listing(l, i, cm, risk_flags=["missing_battery_health"])
    # missing_battery_health permits a lower conservative
    # (or equal — but should never exceed clean case)
    assert v_missing.conservative_resale <= v_clean.conservative_resale + 0.01


def test_combined_missing_bh_and_low_bh_uses_hardest_floor():
    """If both soft and hard signals fire, the hard floor wins."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked 75% BH")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm, risk_flags=[
        "missing_battery_health", "low_battery_health",
    ])
    # Hard signal present → floor disabled
    assert "anchor_floor_disabled_for_defects" in v.warnings
