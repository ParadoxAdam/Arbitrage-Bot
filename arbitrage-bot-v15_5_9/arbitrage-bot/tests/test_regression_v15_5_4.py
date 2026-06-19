"""
v15.5.4 — propagate v1/v2 estimates onto NearMiss objects so the
Top Failed dashboard tab can show them.
"""
from app.pricing.comps import NearMiss, add_near_miss, get_near_misses, reset_near_misses


def test_near_miss_carries_v1_v2_fields():
    reset_near_misses()
    add_near_miss(NearMiss(
        title="Test", url="http://x", price=100, shipping=0,
        expected_resale=200, net_profit=50, roi=0.5,
        score=0.4, confidence=0.5, match_quality=0.85,
        comp_source="active", comp_count=10, category="phones",
        fail_reason="ROI 16% < 20%",
        is_genuine_near_miss=True,
        v1_expected_resale=190.0,
        v2_expected_resale=200.0,
        valuation_method="active_plus_reference",
        valuation_warnings=["own_outcomes_not_available"],
    ))
    misses = get_near_misses(limit=5)
    assert len(misses) == 1
    d = misses[0].to_dict()
    assert d["v1_expected_resale"] == 190.0
    assert d["v2_expected_resale"] == 200.0
    assert d["valuation_method"] == "active_plus_reference"
    assert d["valuation_warnings"] == ["own_outcomes_not_available"]


def test_near_miss_optional_v1_v2_default_to_none():
    """Old code paths that don't pass v1/v2 still work — fields default to None."""
    reset_near_misses()
    add_near_miss(NearMiss(
        title="Test", url="http://x", price=100, shipping=0,
        expected_resale=200, net_profit=50, roi=0.5,
        score=0.4, confidence=0.5, match_quality=0.85,
        comp_source="active", comp_count=10, category="phones",
        fail_reason="x",
    ))
    misses = get_near_misses(limit=5)
    d = misses[0].to_dict()
    assert d["v1_expected_resale"] is None
    assert d["v2_expected_resale"] is None
    assert d["valuation_method"] is None
