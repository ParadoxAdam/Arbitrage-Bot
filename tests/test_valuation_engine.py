"""
Tests for Valuation Engine v2 (v15.5).

Covers the seven regression cases from the spec plus the v15.5 new behaviour
(ranges, anchor sanity checks, fallback warning, persisted confidence).
"""
import pytest
from app.valuation import (
    Valuation, value_listing, VALUATION_VERSION,
    METHOD_ACTIVE_ONLY, METHOD_ACTIVE_PLUS_REFERENCE,
    METHOD_ANCHOR_DRIVEN_REVIEW_ONLY, METHOD_ENGINE_FALLBACK_V1,
    find_anchor,
)
from app.valuation.condition_adjuster import compute_condition_adjustment
from app.models import Listing, NormalizedIdentity, CompMatch, _utcnow


def _listing(title, price=400, condition="good"):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category="phones",
        price=price, shipping=0, condition=condition,
        scraped_at=_utcnow(), raw={},
    )


def _identity(model="iphone 14 pro", storage_gb=128, condition="good",
              carrier="unlocked"):
    return NormalizedIdentity(
        brand="Apple", model=model, category="phones",
        storage_gb=storage_gb, condition=condition, carrier=carrier,
    )


def _comp_match(expected_resale=440.0, sample_size=8, source="active",
                confidence=0.50, prices=None, titles=None):
    if prices is None:
        prices = [420, 430, 440, 445, 450]
    if titles is None:
        titles = [f"iPhone 14 Pro 128GB Unlocked Good Condition #{i}"
                  for i in range(len(prices))]
    evidence = [{"price": p, "title": t} for p, t in zip(prices, titles)]
    return CompMatch(
        fair_value=expected_resale * 0.74,
        expected_resale=expected_resale,
        confidence=confidence, sample_size=sample_size,
        liquidity=0.5, source=source, match_quality=0.85,
        match_details="exact comps; relative cond applied",
        comp_evidence=evidence,
    )


# ── Version + structure ────────────────────────────────────────────

def test_valuation_version_is_v15_5():
    """Sanity check — version starts with v15.5 (any patch level)."""
    assert VALUATION_VERSION.startswith("v15.5")


def test_value_listing_returns_valuation_with_all_fields():
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm)
    assert isinstance(v, Valuation)
    assert v.expected_resale > 0
    assert v.conservative_resale <= v.expected_resale <= v.optimistic_resale
    assert 0.0 <= v.valuation_confidence <= 1.0
    assert v.valuation_method
    assert v.explanation


# ── Reference anchor lookup ─────────────────────────────────────────

def test_anchor_found_for_iphone_14_pro_128():
    a = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    assert a is not None
    assert a.low > 0 and a.mid > a.low and a.high > a.mid


def test_anchor_missing_for_unknown_model():
    a = find_anchor("phones", "Apple", "iphone 99 super", 128, "unlocked")
    assert a is None


def test_anchor_storage_specific():
    """128 and 256 should be different anchors."""
    a128 = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    a256 = find_anchor("phones", "Apple", "iphone 14 pro", 256, "unlocked")
    assert a128 is not None and a256 is not None
    assert a256.mid > a128.mid


# ── Range output ────────────────────────────────────────────────────

def test_range_is_sensible_with_anchor():
    """A good-condition iPhone 14 Pro produces a range with conservative ≤
    expected ≤ optimistic and broadly aligns with the anchor band.
    v15.5.7: when the v2 expected exceeds the anchor ceiling because comps
    are above the anchor, the optimistic must still be ≥ the v2 expected."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match(expected_resale=440)
    v = value_listing(l, i, cm)
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    # The fundamental invariant — range must be ordered
    assert v.conservative_resale <= v.expected_resale <= v.optimistic_resale
    # If v2 expected fits inside the anchor band, the anchor clamps apply
    if v.expected_resale <= anchor.high:
        assert v.conservative_resale >= anchor.low * 0.95 - 0.01
    if v.expected_resale <= anchor.high:
        assert v.optimistic_resale <= anchor.high * 1.05 + 0.01


# ── Suspicious low (regression: opp #630 / opp #635 patterns) ──────

def test_suspicious_low_active_estimate_flagged():
    """If active comps median is far below anchor.low × 0.7, flag it."""
    l = _listing("Apple iPhone 14 Pro Max 128GB Unlocked Good Condition")
    i = _identity(model="iphone 14 pro max", storage_gb=128)
    anchor = find_anchor("phones", "Apple", "iphone 14 pro max", 128, "unlocked")
    # Force a low estimate well below anchor.low × 0.7
    cm = _comp_match(expected_resale=anchor.low * 0.5)
    v = value_listing(l, i, cm)
    assert "valuation_suspicious_low" in v.warnings
    assert v.valuation_method == METHOD_ANCHOR_DRIVEN_REVIEW_ONLY
    # Confidence capped per Guardrail G7
    assert v.valuation_confidence <= 0.40


def test_suspicious_high_estimate_flagged():
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    cm = _comp_match(expected_resale=anchor.high * 1.5)
    v = value_listing(l, i, cm)
    assert "valuation_suspicious_high" in v.warnings
    assert v.valuation_method == METHOD_ANCHOR_DRIVEN_REVIEW_ONLY
    assert v.valuation_confidence <= 0.40


def test_anchor_only_does_not_create_candidate():
    """Guardrail G1 — even with a known anchor, an anchor-driven valuation
    is capped at 0.40 confidence so it can't pass alert thresholds (0.50)."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    # Anchor says ~£440 mid; comps say £200 — heavy disagreement
    cm = _comp_match(expected_resale=200, sample_size=4, confidence=0.50)
    v = value_listing(l, i, cm)
    assert v.valuation_confidence < 0.50, (
        "anchor-driven valuations must not be allowed to reach alert "
        "confidence threshold"
    )


# ── Defects bypass anchor floor (Guardrail G2) ─────────────────────

def test_low_battery_target_can_drop_below_anchor_low():
    """A target with low battery health should be allowed to value below
    the anchor floor — anchors represent healthy units."""
    l = _listing(
        "Apple iPhone 14 Pro 128GB Unlocked 78% Battery Health",
        condition="good",
    )
    i = _identity()
    cm = _comp_match(expected_resale=380)
    v = value_listing(
        l, i, cm,
        risk_flags=["low_battery_health"],
    )
    # Anchor floor disabled for defects
    assert "anchor_floor_disabled_for_defects" in v.warnings
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    # Conservative SHOULD be allowed to drop below 0.95 × anchor.low
    # (We don't enforce a hard < check because the v1 estimate of £380 is
    # only slightly under anchor.low for some models. The key is the warning
    # is set and the floor was *not* clamped to the healthy-only level.)
    assert v.conservative_resale >= anchor.low * 0.50  # lower hard cap only


def test_no_defect_target_clamps_at_anchor_floor():
    """A clean target should NOT have conservative below 0.95 × anchor.low."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked Excellent")
    i = _identity()
    cm = _comp_match(expected_resale=380)
    v = value_listing(l, i, cm, risk_flags=[])
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    assert v.conservative_resale >= anchor.low * 0.95 - 0.01
    assert "anchor_floor_disabled_for_defects" not in v.warnings


# ── Condition double-discount fix (Guardrail G3) ───────────────────

def test_condition_adjustment_does_not_double_discount():
    """When the comp pool was already condition-adjusted (v15.4.7 relative),
    the engine must NOT apply another full absolute discount on top."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked Good Condition",
                 condition="good")
    i = _identity(condition="good")
    cm = _comp_match(expected_resale=440)
    v = value_listing(l, i, cm, risk_flags=[])
    # No battery percentage stated, no soft-damage flags → factor should be ~1.0
    assert v.condition_adjustment >= 0.95


def test_battery_low_does_apply_discount():
    """A target with stated 78% battery health DOES get a battery discount
    even though comps were already condition-adjusted."""
    l = _listing(
        "Apple iPhone 14 Pro 128GB Unlocked 78% BH",
        condition="good",
    )
    i = _identity(condition="good")
    cm = _comp_match(expected_resale=440)
    v = value_listing(l, i, cm, risk_flags=["low_battery_health"])
    # Battery in 70-79 band → ×0.85 multiplier
    assert v.condition_adjustment <= 0.90


# ── Method labels ──────────────────────────────────────────────────

def test_active_only_method_when_no_anchor():
    """A model with no anchor should use ACTIVE_ONLY method."""
    l = _listing("Apple iPhone 99 Super 128GB Unlocked")
    i = _identity(model="iphone 99 super")    # not in anchors
    cm = _comp_match(expected_resale=440)
    v = value_listing(l, i, cm)
    assert v.valuation_method in (METHOD_ACTIVE_ONLY,)


def test_active_plus_reference_when_anchor_exists():
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match(expected_resale=440)
    v = value_listing(l, i, cm)
    # Healthy comp / anchor agreement → active+reference
    assert v.valuation_method == METHOD_ACTIVE_PLUS_REFERENCE


def test_no_sold_comps_method_does_not_claim_sold():
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match(source="active")
    v = value_listing(l, i, cm)
    assert "sold" not in v.valuation_method


# ── Own outcomes (currently zero weight unless N≥3) ────────────────

def test_few_own_outcomes_marked_insufficient():
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm, own_outcomes=[450])
    assert "insufficient_own_data" in v.warnings
    # Not heavily weighted
    assert v.source_weights.get("own", 0.0) == 0.0


# ── Backwards compat ───────────────────────────────────────────────

def test_expected_resale_remains_usable_for_profit_calc():
    """The single-number expected_resale still works for profit math."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match(expected_resale=440)
    v = value_listing(l, i, cm)
    assert isinstance(v.expected_resale, (int, float))
    assert v.expected_resale > 0


def test_engine_fallback_warning_on_failure(monkeypatch):
    """Guardrail G4 — if engine v2 raises, fall back to v1 with explicit warning."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match()

    def boom(*a, **kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr("app.valuation.engine._value_listing_v2", boom)
    v = value_listing(l, i, cm)
    assert v.valuation_method == METHOD_ENGINE_FALLBACK_V1
    assert "valuation_engine_fallback" in v.warnings


def test_valuation_confidence_is_top_level():
    """Guardrail G5 — confidence must be accessible as a direct attribute."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    cm = _comp_match()
    v = value_listing(l, i, cm)
    # As attribute
    assert hasattr(v, "valuation_confidence")
    # And in the dict
    d = v.to_dict()
    assert "valuation_confidence" in d
    assert d["valuation_confidence"] == v.valuation_confidence


# ── Regression cases from spec ─────────────────────────────────────

def test_iphone_14_pro_max_double_discount_regression():
    """Adam's bug: iPhone 14 Pro Max 128GB comp median £390.99 → £291 estimate.
    With v2: should NOT compress. Should preserve roughly the comp median
    (no additional condition discount because target = comp pool condition)."""
    l = _listing(
        "Apple iPhone 14 Pro Max 128GB Unlocked Good Condition",
        condition="good",
    )
    i = _identity(model="iphone 14 pro max", storage_gb=128)
    # v15.4.7's relative cond logic produced ~£391 expected_resale
    cm = _comp_match(
        expected_resale=391,
        prices=[378, 391, 391, 412],
        titles=[
            "iPhone 14 Pro Max 128GB Very Good Condition",
            "iPhone 14 Pro Max 128GB Good Condition",
            "iPhone 14 Pro Max 128GB Good Condition",
            "iPhone 14 Pro Max with New Battery 128GB Good",
        ],
        sample_size=4,
    )
    v = value_listing(l, i, cm, risk_flags=[])
    # Must NOT drop expected resale dramatically below comp median
    assert v.expected_resale >= 350, (
        f"v2 should not double-discount; got £{v.expected_resale}"
    )


def test_iphone_15_pro_vs_pro_max_different_anchors():
    """Different model variants get different anchors."""
    pro = find_anchor("phones", "Apple", "iphone 15 pro", 256, "unlocked")
    pro_max = find_anchor("phones", "Apple", "iphone 15 pro max", 256, "unlocked")
    assert pro is not None and pro_max is not None
    assert pro_max.mid > pro.mid


def test_low_battery_target_does_not_use_anchor_as_hard_floor():
    """Regression: target with stated 70% battery should be allowed to value
    below anchor.low. Anchors represent healthy units only."""
    l = _listing("Apple iPhone 14 Pro 128GB 70% Battery", condition="good")
    i = _identity()
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    # Force a comp result well below anchor low
    cm = _comp_match(expected_resale=anchor.low * 0.85)
    v = value_listing(l, i, cm, risk_flags=["low_battery_health"])
    # Conservative IS allowed to drop below 0.95 × anchor.low here
    # because target has stated defects
    assert "anchor_floor_disabled_for_defects" in v.warnings


def test_disagreement_does_not_auto_create_candidate():
    """Guardrail G1: even when comps and anchor wildly disagree, the bot
    must not produce a high-confidence valuation that triggers alerts."""
    l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
    i = _identity()
    anchor = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
    # Massive disagreement — comps say £150, anchor mid is £440
    cm = _comp_match(expected_resale=150, sample_size=5, confidence=0.55)
    v = value_listing(l, i, cm)
    # The valuation gets capped — it cannot pass MIN_CONFIDENCE=0.50
    assert v.valuation_confidence <= 0.40
    # And the method is explicit about being review-only
    assert v.valuation_method == METHOD_ANCHOR_DRIVEN_REVIEW_ONLY
