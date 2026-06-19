"""
v15.5.6 — NearMiss carries the full Valuation breakdown so the Top Failed
tab can render the same collapsible breakdown section as the Review Queue.
"""
from app.pricing.comps import NearMiss


def test_near_miss_carries_breakdown_fields():
    """All breakdown fields needed by renderValuationBreakdown(c) are present."""
    breakdown = {
        "valuation_method": "active_plus_reference",
        "v1_expected_resale": 388.73,
        "expected_resale": 481.12,
        "raw_active_comp_median": 440.0,
        "active_listing_discount": 0.88,
        "active_comp_count": 13,
        "active_comp_spread": 0.05,
        "reference_anchor_low": 720.0,
        "reference_anchor_mid": 820.0,
        "reference_anchor_high": 950.0,
        "reference_anchor_source": "UK eBay survey v15.5",
        "source_weights": {"active": 0.7, "anchor": 0.3},
        "condition_adjustment": 1.0,
        "condition_reasons": [],
        "liquidity_band": "high",
        "liquidity_score": 0.85,
        "warnings": ["valuation_suspicious_low"],
        "explanation": "v1 active median £440",
    }
    nm = NearMiss(
        title="iPhone 15 Pro Max", url="http://x",
        price=343.64, shipping=0,
        expected_resale=481.12, net_profit=59.01, roi=0.172,
        score=0.43, confidence=0.40, match_quality=0.85,
        comp_source="active", comp_count=13, category="phones",
        fail_reason="ROI 17.2% < 20%",
        is_genuine_near_miss=True,
        v1_expected_resale=388.73,
        v2_expected_resale=481.12,
        valuation_method="anchor_driven_review_only",
        valuation_warnings=["valuation_suspicious_low"],
        valuation_confidence=0.40,
        conservative_resale=440.0,
        optimistic_resale=520.0,
        valuation_breakdown=breakdown,
    )
    d = nm.to_dict()
    # All breakdown-related fields exposed for the dashboard
    assert d["valuation_breakdown"] == breakdown
    assert d["conservative_resale"] == 440.0
    assert d["optimistic_resale"] == 520.0
    assert d["valuation_confidence"] == 0.40
    # And the existing v1/v2 + method/warnings still work
    assert d["v1_expected_resale"] == 388.73
    assert d["v2_expected_resale"] == 481.12
    assert d["valuation_method"] == "anchor_driven_review_only"
    assert d["valuation_warnings"] == ["valuation_suspicious_low"]


def test_near_miss_breakdown_optional_defaults_none():
    """Old call sites that don't pass breakdown still work."""
    nm = NearMiss(
        title="x", url="http://x", price=100, shipping=0,
        expected_resale=200, net_profit=50, roi=0.5,
        score=0.4, confidence=0.5, match_quality=0.85,
        comp_source="active", comp_count=10, category="phones",
        fail_reason="x",
    )
    d = nm.to_dict()
    assert d["valuation_breakdown"] is None
    assert d["conservative_resale"] is None
    assert d["optimistic_resale"] is None
    assert d["valuation_confidence"] is None
