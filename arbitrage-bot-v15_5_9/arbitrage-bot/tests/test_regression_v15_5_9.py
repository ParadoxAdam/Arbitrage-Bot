"""
v15.5.9 — Target buy price / negotiation feature.

Asserts:
  1. Versions bumped correctly (APP_VERSION advances; VALUATION_VERSION
     deliberately does NOT, because no scoring/comping logic changed).
  2. /near-misses includes a `negotiation` block per row.
  3. /review (review queue) includes a `negotiation` block per candidate.
  4. /analytics/negotiation returns the bucket summary shape.
  5. Settings expose negotiation discount limits.
  6. Top Failed pipeline propagates structured failure_reasons + risk_flags
     into NearMiss objects.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import APP_VERSION, VALUATION_VERSION, settings
from app.db import init_db, session_scope
from app.main import app
from app.models import ReviewCandidateRow
from app.pricing.comps import (
    add_near_miss, NearMiss, reset_near_misses,
)


# ── 1. Version invariants ───────────────────────────────────────────

def test_app_version_is_v15_5_9():
    assert APP_VERSION == "v15.5.9"


def test_valuation_version_pinned_to_v15_5_8():
    """v15.5.9 is UI-only — valuation/scoring logic unchanged.
    Pinning VALUATION_VERSION lets analytics keep grouping rows
    from before and after this release together."""
    assert VALUATION_VERSION == "v15.5.8"


def test_app_version_is_at_least_valuation_version():
    """APP_VERSION is allowed to be ahead but never behind."""
    assert APP_VERSION >= VALUATION_VERSION


# ── 2. Settings ─────────────────────────────────────────────────────

def test_negotiation_settings_exist_with_sensible_defaults():
    assert hasattr(settings, "negotiation_max_discount_pct")
    assert hasattr(settings, "negotiation_max_discount_abs")
    assert 0 < settings.negotiation_max_discount_pct < 1
    assert settings.negotiation_max_discount_abs > 0


# ── 3. /near-misses enrichment ──────────────────────────────────────

@pytest.fixture
def client():
    init_db()
    return TestClient(app)


def _seed_near_misses():
    reset_near_misses()
    # Negotiable: small discount needed
    add_near_miss(NearMiss(
        title="iPhone 15 Pro 128GB Unlocked - close",
        url="https://example.com/n1", price=410, shipping=0,
        expected_resale=560, net_profit=35, roi=0.085,
        score=0.45, confidence=0.62, match_quality=0.85,
        comp_source="active", comp_count=8, category="phones",
        fail_reason="profit 35 < 40", is_genuine_near_miss=True,
        failure_reasons=["profit_below_threshold", "active_comps_only"],
        risk_flags=[],
        valuation_confidence=0.65, valuation_warnings=[],
    ))
    # Too expensive
    add_near_miss(NearMiss(
        title="iPhone 13 Pro 256GB - too expensive",
        url="https://example.com/n2", price=620, shipping=0,
        expected_resale=540, net_profit=-160, roi=-0.26,
        score=0.30, confidence=0.7, match_quality=0.80,
        comp_source="active", comp_count=10, category="phones",
        fail_reason="profit -160 < 40",
        is_genuine_near_miss=False,
        failure_reasons=["profit_below_threshold", "roi_below_threshold"],
        risk_flags=[],
        valuation_confidence=0.7, valuation_warnings=[],
    ))
    # Condition risk
    add_near_miss(NearMiss(
        title="iPhone 14 Pro Max 128GB cracked screen",
        url="https://example.com/n3", price=380, shipping=0,
        expected_resale=520, net_profit=60, roi=0.16,
        score=0.40, confidence=0.5, match_quality=0.70,
        comp_source="active", comp_count=4, category="phones",
        fail_reason="ROI 16% < 20%",
        is_genuine_near_miss=False,
        failure_reasons=["roi_below_threshold", "critical_risk_flags"],
        risk_flags=["possible_damage"],
        valuation_confidence=0.55, valuation_warnings=[],
    ))


def test_near_misses_endpoint_includes_negotiation_block(client):
    _seed_near_misses()
    r = client.get("/near-misses")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 3
    for row in rows:
        assert "negotiation" in row, "negotiation block missing"
        n = row["negotiation"]
        assert n is not None
        assert "target_review" in n
        assert "target_alert" in n
        assert "buckets" in n
        # Targets have all the expected keys
        for t in (n["target_review"], n["target_alert"]):
            for k in ("max_buy_for_profit", "max_buy_for_roi",
                     "max_buy_overall", "binding_constraint",
                     "discount_needed_abs", "discount_needed_pct",
                     "label", "current_net_profit", "current_roi"):
                assert k in t, f"missing key {k} in target: {t}"


def test_near_miss_negotiation_label_negotiable_for_close_listing(client):
    _seed_near_misses()
    r = client.get("/near-misses")
    rows = {row["title"]: row for row in r.json()}
    close = rows["iPhone 15 Pro 128GB Unlocked - close"]
    n = close["negotiation"]
    # This listing is engineered to be close — small discount within limits
    assert n["target_review"]["label"] in ("already_passes", "negotiable")


def test_near_miss_negotiation_buckets_include_condition_risk(client):
    _seed_near_misses()
    r = client.get("/near-misses")
    rows = {row["title"]: row for row in r.json()}
    cracked = rows["iPhone 14 Pro Max 128GB cracked screen"]
    buckets = cracked["negotiation"]["buckets"]
    assert buckets["failed_condition_risk"] is True


def test_near_miss_negotiation_robust_to_zero_resale(client):
    """A near-miss with no resale should yield negotiation=None,
    not crash the endpoint."""
    reset_near_misses()
    add_near_miss(NearMiss(
        title="No-resale listing", url="https://example.com/no",
        price=100, shipping=0,
        expected_resale=0,        # bad / missing
        net_profit=-100, roi=-1.0,
        score=0, confidence=0, match_quality=0,
        comp_source="active", comp_count=0, category="phones",
        fail_reason="no comps", is_genuine_near_miss=False,
    ))
    r = client.get("/near-misses")
    assert r.status_code == 200
    assert r.json()[0]["negotiation"] is None


# ── 4. /analytics/negotiation summary ───────────────────────────────

def test_analytics_negotiation_returns_summary_shape(client):
    _seed_near_misses()
    r = client.get("/analytics/negotiation")
    assert r.status_code == 200
    data = r.json()

    # Top-level shape
    for k in ("total_failed_scored", "thresholds",
             "by_negotiation_label", "bucket_counts", "bucket_items"):
        assert k in data, f"missing top-level key {k}"

    # Thresholds reflect the configured settings
    th = data["thresholds"]
    assert th["review_min_profit"] == settings.review_min_profit
    assert th["review_min_roi"] == settings.review_min_roi
    assert th["alert_min_profit"] == settings.min_profit
    assert th["alert_min_roi"] == settings.min_roi
    assert th["max_discount_pct"] == settings.negotiation_max_discount_pct
    assert th["max_discount_abs"] == settings.negotiation_max_discount_abs

    # Counts
    assert data["total_failed_scored"] == 3
    # The cracked-screen entry should fire failed_condition_risk
    assert data["bucket_counts"]["failed_condition_risk"] >= 1


def test_analytics_negotiation_accepts_query_overrides(client):
    """Passing wider discount limits should produce >= as many
    'negotiable' results as the defaults."""
    _seed_near_misses()
    base = client.get("/analytics/negotiation").json()
    wide = client.get(
        "/analytics/negotiation?max_discount_pct=0.50&max_discount_abs=500",
    ).json()
    # With a much wider negotiation window, more listings should
    # qualify as "negotiable" (or stay the same if all already do).
    assert (
        wide["by_negotiation_label"]["negotiable"]
        >= base["by_negotiation_label"]["negotiable"]
    )


def test_analytics_negotiation_empty_pool_returns_zero(client):
    reset_near_misses()
    r = client.get("/analytics/negotiation")
    assert r.status_code == 200
    data = r.json()
    assert data["total_failed_scored"] == 0
    assert all(v == 0 for v in data["bucket_counts"].values())


# ── 5. Review queue enrichment ──────────────────────────────────────

def test_review_endpoint_includes_negotiation_block(client):
    """Insert a real candidate row and confirm /review serializes
    it with a negotiation block."""
    init_db()
    with session_scope() as s:
        # Insert a synthetic candidate
        c = ReviewCandidateRow(
            title="Test iPhone 14 Pro 128GB Unlocked",
            source="ebay", source_url="https://example.com/x",
            category="phones", condition="good",
            price=420.0, shipping=0.0,
            fair_value=400, expected_resale=540,
            net_profit=60, roi=0.14, confidence=0.6, liquidity=0.5,
            score=0.30, risk_flags=[], comp_source="active", comp_count=5,
            match_quality=0.8, match_details="exact",
            comp_evidence=[],
            why_passed="for testing", penalties_applied=[],
            decision="pending", lifecycle_stage="none",
            watchlist=False, is_mock=False, status="pending",
            dedupe_key="negotiation_test_key_v15_5_9",
            engine_version=VALUATION_VERSION,
            valuation_confidence=0.6, valuation_warnings=[],
            v1_expected_resale=540, v2_expected_resale=540,
        )
        s.add(c)
        s.flush()
        cid = c.id

    try:
        r = client.get("/review?include_mock=false")
        assert r.status_code == 200
        # Find our synthetic row
        rows = [row for row in r.json() if row["id"] == cid]
        assert rows, "synthetic candidate not returned"
        row = rows[0]
        assert "negotiation" in row
        n = row["negotiation"]
        assert n is not None
        assert "target_review" in n and "target_alert" in n
    finally:
        with session_scope() as s:
            s.query(ReviewCandidateRow).filter_by(id=cid).delete()


def test_review_negotiation_is_none_for_mock_rows(client):
    """Mock rows should not get negotiation analysis (they pollute
    analytics and aren't real flips)."""
    init_db()
    with session_scope() as s:
        c = ReviewCandidateRow(
            title="Mock candidate v15.5.9",
            source="mock", source_url="https://example.com/mock",
            category="phones", condition="good",
            price=100.0, shipping=0.0,
            fair_value=100, expected_resale=200,
            net_profit=50, roi=0.50, confidence=0.5, liquidity=0.5,
            score=0.5, risk_flags=[], comp_source="active", comp_count=5,
            match_quality=0.8, match_details="mock",
            comp_evidence=[], why_passed="mock",
            penalties_applied=[],
            decision="pending", lifecycle_stage="none",
            watchlist=False, is_mock=True, status="pending",
            dedupe_key="negotiation_test_mock_v15_5_9",
            engine_version=VALUATION_VERSION,
        )
        s.add(c)
        s.flush()
        cid = c.id

    try:
        r = client.get("/review?include_mock=true")
        assert r.status_code == 200
        rows = [row for row in r.json() if row["id"] == cid]
        assert rows, "mock candidate missing"
        assert rows[0]["negotiation"] is None
    finally:
        with session_scope() as s:
            s.query(ReviewCandidateRow).filter_by(id=cid).delete()


# ── 6. NearMiss carries structured codes ────────────────────────────

def test_near_miss_carries_failure_reasons_and_risk_flags():
    """v15.5.9: NearMiss must accept and round-trip structured codes
    (the negotiation analyser depends on them for bucketing)."""
    reset_near_misses()
    add_near_miss(NearMiss(
        title="t", url="https://x/", price=1.0, shipping=0.0,
        expected_resale=1.0, net_profit=0.0, roi=0.0, score=0.0,
        confidence=0.0, match_quality=0.0, comp_source="active",
        comp_count=0, category="phones", fail_reason="t",
        is_genuine_near_miss=False,
        failure_reasons=["profit_below_threshold", "roi_below_threshold"],
        risk_flags=["possible_damage"],
    ))
    from app.pricing.comps import get_near_misses
    nm = get_near_misses()[0]
    assert nm.failure_reasons == [
        "profit_below_threshold", "roi_below_threshold",
    ]
    assert nm.risk_flags == ["possible_damage"]


def test_near_miss_optional_fields_default_to_none():
    """Old call sites that omit failure_reasons/risk_flags must still
    work — defaults to None."""
    reset_near_misses()
    add_near_miss(NearMiss(
        title="t", url="https://x/", price=1.0, shipping=0.0,
        expected_resale=1.0, net_profit=0.0, roi=0.0, score=0.0,
        confidence=0.0, match_quality=0.0, comp_source="active",
        comp_count=0, category="phones", fail_reason="t",
        is_genuine_near_miss=False,
    ))
    from app.pricing.comps import get_near_misses
    nm = get_near_misses()[0]
    assert nm.failure_reasons is None
    assert nm.risk_flags is None
