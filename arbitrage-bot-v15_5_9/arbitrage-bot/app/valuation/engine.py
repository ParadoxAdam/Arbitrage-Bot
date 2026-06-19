"""
Valuation Engine v2 (v15.5).

Wraps the existing CompMatch produced by app/pricing/comps.py and adds:
  - conservative / expected / optimistic resale ranges
  - reference-anchor sanity checks
  - explicit valuation method label
  - source weights breakdown
  - condition + battery + liquidity signals
  - structured warnings + human-readable explanation

Guardrails (per spec):
  G1. Reference anchors NEVER create candidates by themselves. They only
      stabilise (widen ranges) and flag suspicious comp-driven estimates.
  G2. Conservative resale CAN fall below reference_anchor_low when the
      target has stated defects (low battery, possible damage, etc.).
      Anchors represent healthy units only.
  G3. We never apply a second absolute condition discount on top of
      v15.4.7's relative comp-pool adjustment — only battery + functional
      damage deltas.
  G4. If the engine cannot produce a v2 valuation, it falls back to the
      original CompMatch and adds a "valuation_engine_fallback" warning.
  G5. valuation_confidence is exposed both as a top-level field on the
      Valuation object and inside the breakdown JSON.
  G6. VALUATION_VERSION is sourced from app.config — single source of truth.
      Persisted version always matches the actual current valuation version.
  G7. Anchor-driven valuations (active comps weak/disagree heavily) are
      capped at 0.40 confidence and tagged "anchor_driven_review_only".
"""
from __future__ import annotations
import logging
import statistics
from dataclasses import dataclass, field
from typing import Any
from ..config import VALUATION_VERSION   # v15.5.2: single source of truth
from .reference_anchors import find_anchor, find_anchor_loose, ReferenceAnchor
from .condition_adjuster import compute_condition_adjustment
from .liquidity import compute_liquidity, LiquiditySignal

log = logging.getLogger("valuation")


# Method labels (exposed in API + dashboard)
METHOD_ACTIVE_ONLY = "active_only"
METHOD_ACTIVE_PLUS_REFERENCE = "active_plus_reference"
METHOD_SOLD_PLUS_ACTIVE = "sold_plus_active"
METHOD_OWN_OUTCOME_PLUS_MARKET = "own_outcome_plus_market"
METHOD_FALLBACK_LOW_CONFIDENCE = "fallback_low_confidence"
METHOD_ANCHOR_DRIVEN_REVIEW_ONLY = "anchor_driven_review_only"
METHOD_ENGINE_FALLBACK_V1 = "engine_fallback_v1"


@dataclass
class Valuation:
    """
    Full v2 valuation output.

    Field naming convention (v15.5.1):
      v1_*                — produced by the legacy CompMatch (comps.py)
      v2_*                — produced by Valuation Engine v2 blending
      active_*            — raw eBay active comp data (NOT condition-adjusted)
      reference_anchor_*  — manual market anchors
    """
    valuation_version: str = VALUATION_VERSION
    valuation_method: str = METHOD_ACTIVE_ONLY

    # ── Range ──────────────────────────────────────────────────
    conservative_resale: float = 0.0
    # Canonical "expected resale". Profit math uses this when
    # settings.use_v2_for_profit=True; otherwise profit uses the v1 estimate.
    expected_resale: float = 0.0
    optimistic_resale: float = 0.0

    # Confidence (top-level for easy access)
    valuation_confidence: float = 0.0

    # ── Source contributions ───────────────────────────────────
    source_weights: dict[str, float] = field(default_factory=dict)

    # v1 single-number estimate that the engine started from
    # (already condition-adjusted by comps.py's relative logic).
    v1_expected_resale: float | None = None

    # Raw active-comp median from eBay BEFORE any active discount or
    # condition adjustment. Useful for debugging "what is the market saying?"
    raw_active_comp_median: float | None = None
    # Multiplier applied by comps.py for active vs sold drift
    # (e.g. 0.88 for phones). Surfaced for transparency.
    active_listing_discount: float | None = None

    active_comp_count: int = 0
    active_comp_spread: float | None = None     # coefficient of variation

    sold_comp_median: float | None = None
    sold_comp_count: int = 0

    own_outcome_average: float | None = None
    own_outcome_count: int = 0

    reference_anchor_low: float | None = None
    reference_anchor_mid: float | None = None
    reference_anchor_high: float | None = None
    reference_anchor_source: str | None = None

    # ── Adjustments ────────────────────────────────────────────
    condition_adjustment: float = 1.0
    condition_reasons: list[str] = field(default_factory=list)
    battery_adjustment: float | None = None
    liquidity_band: str = "unknown"
    liquidity_score: float = 0.0
    liquidity_reasons: list[str] = field(default_factory=list)

    # ── Diagnostics ────────────────────────────────────────────
    warnings: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict:
        return {
            "valuation_version": self.valuation_version,
            "valuation_method": self.valuation_method,
            "conservative_resale": self.conservative_resale,
            "expected_resale": self.expected_resale,
            "optimistic_resale": self.optimistic_resale,
            "valuation_confidence": self.valuation_confidence,
            "source_weights": self.source_weights,
            # v15.5.1 — explicit naming
            "v1_expected_resale": self.v1_expected_resale,
            "raw_active_comp_median": self.raw_active_comp_median,
            "active_listing_discount": self.active_listing_discount,
            "active_comp_count": self.active_comp_count,
            "active_comp_spread": self.active_comp_spread,
            "sold_comp_median": self.sold_comp_median,
            "sold_comp_count": self.sold_comp_count,
            "own_outcome_average": self.own_outcome_average,
            "own_outcome_count": self.own_outcome_count,
            "reference_anchor_low": self.reference_anchor_low,
            "reference_anchor_mid": self.reference_anchor_mid,
            "reference_anchor_high": self.reference_anchor_high,
            "reference_anchor_source": self.reference_anchor_source,
            "condition_adjustment": self.condition_adjustment,
            "condition_reasons": self.condition_reasons,
            "battery_adjustment": self.battery_adjustment,
            "liquidity_band": self.liquidity_band,
            "liquidity_score": self.liquidity_score,
            "liquidity_reasons": self.liquidity_reasons,
            "warnings": list(self.warnings),
            "explanation": self.explanation,
        }


# ────────────────────────────────────────────────────────────────────
# Main entry
# ────────────────────────────────────────────────────────────────────

def value_listing(
    listing,
    identity,
    comp_match,
    *,
    risk_flags: list[str] | None = None,
    own_outcomes: list[float] | None = None,
) -> Valuation:
    """
    Produce a Valuation v2 from the existing CompMatch.

    The CompMatch already embeds v15.4.7's relative-condition active comp
    expected_resale. We treat that as our "comps_v1_estimate" input and
    layer:
      - reference-anchor sanity check
      - battery/functional damage adjustment
      - sold-comp blend (when available — currently 0 weight)
      - own-outcome blend (when available — currently 0 weight unless N≥3)
      - liquidity signal
      - range construction
    """
    risk_flags = risk_flags or []
    own_outcomes = own_outcomes or []

    try:
        return _value_listing_v2(listing, identity, comp_match,
                                 risk_flags, own_outcomes)
    except Exception as e:
        log.warning("Valuation engine v2 failed; falling back to v1: %s", e)
        # Guardrail G4: explicit fallback warning
        v = Valuation(
            valuation_version=VALUATION_VERSION,
            valuation_method=METHOD_ENGINE_FALLBACK_V1,
            expected_resale=comp_match.expected_resale,
            conservative_resale=round(comp_match.expected_resale * 0.92, 2),
            optimistic_resale=round(comp_match.expected_resale * 1.08, 2),
            valuation_confidence=comp_match.confidence,
            warnings=["valuation_engine_fallback"],
            explanation=f"Engine v2 raised {type(e).__name__}; using v1 comp_match.",
        )
        v.v1_expected_resale = round(comp_match.expected_resale, 2)
        return v


def _value_listing_v2(listing, identity, comp_match,
                      risk_flags, own_outcomes) -> Valuation:
    v = Valuation(valuation_version=VALUATION_VERSION)

    # ── Inputs from CompMatch ──────────────────────────────
    v1_estimate = comp_match.expected_resale
    is_sold_source = comp_match.source == "sold"
    v1_conf = comp_match.confidence
    v.v1_expected_resale = round(v1_estimate, 2)         # v15.5.1: explicit
    v.active_comp_count = comp_match.sample_size

    # Reverse-engineer the raw active-comp median from the v1 estimate.
    # comps.py applies: active_discount × cond_adjustment_relative.
    # We can only recover the raw median if the comp_match exposes evidence prices.
    spread_cv = _spread_cv(comp_match)
    v.active_comp_spread = spread_cv
    raw_median = _raw_median_from_evidence(comp_match)
    v.raw_active_comp_median = raw_median

    # Surface the active-listing discount that comps.py applied.
    # We import lazily to avoid a hard dep on comps internals.
    try:
        from ..pricing.comps import ACTIVE_LISTING_DISCOUNT
        v.active_listing_discount = ACTIVE_LISTING_DISCOUNT.get(
            identity.category, None,
        )
    except Exception:
        v.active_listing_discount = None

    if is_sold_source:
        v.sold_comp_median = v1_estimate
        v.sold_comp_count = comp_match.sample_size

    # ── Reference anchor lookup (v15.5.2: three-state carrier) ─
    target_carrier = (identity.carrier or "").lower().strip()
    anchor: ReferenceAnchor | None = None
    anchor_carrier_unknown = False

    if target_carrier and "unlocked" in target_carrier:
        # Explicit unlocked → exact anchor match
        anchor = find_anchor(
            category=identity.category,
            brand=identity.brand,
            model=identity.model,
            storage_gb=identity.storage_gb,
            carrier="unlocked",
        )
    elif target_carrier:
        # Locked / carrier-specific → only use anchor if a carrier-specific
        # anchor exists (currently none do, so this returns None).
        anchor = find_anchor(
            category=identity.category,
            brand=identity.brand,
            model=identity.model,
            storage_gb=identity.storage_gb,
            carrier=target_carrier,
        )
        # No locked-carrier anchor available — that's fine, valuation
        # proceeds without the stabiliser.
    else:
        # Carrier unknown → use the loose unlocked anchor as a weak
        # stabiliser only, and warn the caller.
        anchor = find_anchor_loose(
            category=identity.category,
            brand=identity.brand,
            model=identity.model,
            storage_gb=identity.storage_gb,
        )
        if anchor:
            anchor_carrier_unknown = True
            v.warnings.append("carrier_unknown_anchor_weak")

    if anchor:
        v.reference_anchor_low = anchor.low
        v.reference_anchor_mid = anchor.mid
        v.reference_anchor_high = anchor.high
        v.reference_anchor_source = anchor.source_label

    # ── Condition + battery adjustment ─────────────────────
    # Important: comp_pool_already_relative=True so we DO NOT add a second
    # absolute condition discount on top of v15.4.7's relative logic.
    cond_adj = compute_condition_adjustment(
        target_condition=identity.condition,
        risk_flags=risk_flags,
        title=listing.title,
        comp_pool_already_relative=True,
    )
    v.condition_adjustment = cond_adj.factor
    v.condition_reasons = cond_adj.reasons

    # Track battery factor separately for the dashboard
    if any("battery" in r for r in cond_adj.reasons):
        v.battery_adjustment = cond_adj.factor
    elif cond_adj.factor < 1.0:
        v.battery_adjustment = None

    # Apply condition deltas on top of v1 estimate
    v1_after_cond = v1_estimate * cond_adj.factor

    # ── Sold + own-outcome blending (currently low weight) ─
    sold_w = 0.0
    own_w = 0.0
    if is_sold_source:
        sold_w = 0.50              # high trust when MI lands
    if len(own_outcomes) >= 3:
        own_w = 0.30
        v.own_outcome_average = round(statistics.fmean(own_outcomes), 2)
        v.own_outcome_count = len(own_outcomes)
    elif len(own_outcomes) > 0:
        v.own_outcome_count = len(own_outcomes)
        v.warnings.append("insufficient_own_data")
        # Don't weight — show in breakdown only
    else:
        # v15.5.1: explicit signal that no historical sale data was used
        v.warnings.append("own_outcomes_not_available")

    # Active is whatever's left after sold + own
    active_w = max(0.05, 1.0 - sold_w - own_w)

    # Anchor weight: small stabiliser, only when anchor exists AND no
    # sold/own data dominates
    anchor_w = 0.0
    if anchor and not is_sold_source and own_w == 0.0:
        # Default anchor weight; raised when comps disagree (handled below)
        anchor_w = 0.10
        # v15.5.2: weak anchor when carrier is unknown
        if anchor_carrier_unknown:
            anchor_w = 0.05    # halve the stabiliser
            # Cap baseline confidence — we can't trust an unlocked anchor
            # for an unknown-carrier listing
            v1_conf = min(v1_conf, 0.45)

    # Re-normalise so weights sum to 1
    total_w = active_w + sold_w + own_w + anchor_w
    if total_w > 0:
        active_w /= total_w
        sold_w /= total_w
        own_w /= total_w
        anchor_w /= total_w

    # ── Anchor sanity checks ───────────────────────────────
    is_anchor_driven = False
    if anchor:
        # Compare v1_after_cond to anchor range
        if v1_after_cond < anchor.low * 0.70:
            v.warnings.append("valuation_suspicious_low")
            v1_conf = min(v1_conf, 0.40)        # cap confidence
            anchor_w = max(anchor_w, 0.30)      # let anchor pull it up some
            is_anchor_driven = True
        elif v1_after_cond > anchor.high * 1.30:
            v.warnings.append("valuation_suspicious_high")
            v1_conf = min(v1_conf, 0.40)
            anchor_w = max(anchor_w, 0.30)
            is_anchor_driven = True

    # Re-normalise after possible anchor weight bump
    total_w = active_w + sold_w + own_w + anchor_w
    if total_w > 0:
        active_w /= total_w
        sold_w /= total_w
        own_w /= total_w
        anchor_w /= total_w

    # ── Compose the weighted expected_resale ───────────────
    parts = []
    if active_w > 0:
        parts.append(("active", active_w, v1_after_cond))
    if sold_w > 0 and v.sold_comp_median is not None:
        parts.append(("sold", sold_w, v.sold_comp_median * cond_adj.factor))
    if own_w > 0 and v.own_outcome_average is not None:
        parts.append(("own", own_w, v.own_outcome_average))
    if anchor_w > 0 and anchor:
        parts.append(("anchor", anchor_w, anchor.mid))

    if not parts:
        # Pathological — no inputs
        v.valuation_method = METHOD_FALLBACK_LOW_CONFIDENCE
        v.expected_resale = round(v1_estimate, 2)
        v.conservative_resale = round(v1_estimate * 0.85, 2)
        v.optimistic_resale = round(v1_estimate * 1.15, 2)
        v.valuation_confidence = min(v1_conf, 0.30)
        v.warnings.append("no_valuation_inputs")
        v.explanation = "No valid inputs; using v1 estimate at low confidence."
        return v

    weighted_sum = sum(w * val for _, w, val in parts)
    v.expected_resale = round(weighted_sum, 2)
    v.source_weights = {name: round(w, 3) for name, w, _ in parts}

    # ── Method label ───────────────────────────────────────
    if is_anchor_driven:
        v.valuation_method = METHOD_ANCHOR_DRIVEN_REVIEW_ONLY
    elif sold_w > 0 and active_w > 0:
        v.valuation_method = METHOD_SOLD_PLUS_ACTIVE
    elif own_w > 0:
        v.valuation_method = METHOD_OWN_OUTCOME_PLUS_MARKET
    elif anchor_w > 0 and active_w > 0:
        v.valuation_method = METHOD_ACTIVE_PLUS_REFERENCE
    else:
        v.valuation_method = METHOD_ACTIVE_ONLY

    # ── Liquidity ──────────────────────────────────────────
    # Spread fallback when no CV available
    cv_for_liq = spread_cv if spread_cv is not None else 0.20
    liq = compute_liquidity(
        exact_comp_count=comp_match.sample_size,
        partial_comp_count=0,           # could be plumbed through later
        raw_returned=comp_match.sample_size,
        spread_cv=cv_for_liq,
    )
    v.liquidity_band = liq.band
    v.liquidity_score = liq.score
    v.liquidity_reasons = liq.reasons

    # ── Confidence assembly ────────────────────────────────
    confidence = v1_conf - cond_adj.confidence_penalty
    # Anchor-driven valuations get capped at 0.40 (Guardrail G7)
    if is_anchor_driven:
        confidence = min(confidence, 0.40)
    # Pure active-only valuations don't exceed 0.55 (already enforced
    # in v15.4.x but reaffirmed here)
    if v.valuation_method == METHOD_ACTIVE_ONLY:
        confidence = min(confidence, 0.55)
    confidence = max(0.0, min(1.0, confidence))
    v.valuation_confidence = round(confidence, 3)

    # ── Range construction ─────────────────────────────────
    # Spread factor: more comps + tighter spread = narrower range
    spread_factor = _range_spread_factor(comp_match.sample_size, cv_for_liq)

    # Conservative / optimistic
    cons = v.expected_resale * (1 - spread_factor)
    opti = v.expected_resale * (1 + spread_factor)

    # Anchor-aware bounds (Guardrail G2)
    # v15.5.8: missing_battery_health is a SOFT signal (most clean eBay
    # listings don't state BH in the title — that doesn't mean defective).
    # Only treat it as a hard defect when combined with other signals.
    # Hard defects = damage + stated low battery + condition fair/parts.
    risk_set = set(risk_flags)
    has_hard_defect = bool(risk_set & {
        "possible_damage", "low_battery_health",
    }) or identity.condition in ("fair", "parts")
    # Soft signals (BH not stated, accessory_not_product elsewhere) widen
    # the conservative bound but don't fully disable the anchor floor.
    has_soft_signal = "missing_battery_health" in risk_set

    if anchor:
        if has_hard_defect:
            # Real defect signal — disable anchor floor to allow the
            # conservative estimate to drop substantially below the
            # healthy-unit anchor range.
            cons = max(cons, anchor.low * 0.50)
            v.warnings.append("anchor_floor_disabled_for_defects")
        elif has_soft_signal:
            # Just missing BH — soften the floor to anchor.low × 0.85
            # rather than full disable. Phones often sell without stated
            # BH and still have healthy batteries.
            cons = max(cons, anchor.low * 0.85)
        else:
            # Healthy target — full anchor floor protection
            cons = max(cons, anchor.low * 0.95)
        # Optimistic capped above anchor ceiling so we don't get carried
        # away on noisy comps
        opti = min(opti, anchor.high * 1.05)

    # v15.5.7: Ensure conservative ≤ expected ≤ optimistic invariant.
    cons = min(cons, v.expected_resale)
    opti = max(opti, v.expected_resale)

    v.conservative_resale = round(cons, 2)
    v.optimistic_resale = round(opti, 2)

    # ── Explanation ────────────────────────────────────────
    v.explanation = _build_explanation(v, comp_match, anchor)

    return v


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _spread_cv(comp_match) -> float | None:
    """Coefficient of variation of comp evidence prices (if available)."""
    if not comp_match.comp_evidence:
        return None
    prices = [e.get("price", 0) for e in comp_match.comp_evidence
              if e.get("price")]
    if len(prices) < 2:
        return None
    mean = statistics.fmean(prices)
    if mean <= 0:
        return None
    sd = statistics.pstdev(prices)
    return round(sd / mean, 3)


def _raw_median_from_evidence(comp_match) -> float | None:
    """
    Raw median of the comp evidence prices BEFORE any active discount or
    condition adjustment. This is what the eBay market is literally asking,
    surfaced for dashboard transparency.
    """
    if not comp_match.comp_evidence:
        return None
    prices = [e.get("price", 0) for e in comp_match.comp_evidence
              if e.get("price")]
    if not prices:
        return None
    return round(statistics.median(prices), 2)


def _range_spread_factor(sample_size: int, cv: float) -> float:
    """
    How wide should the conservative/optimistic range be?
    Tight comps + many samples → narrow band. Few comps or wide spread → wide band.
    Returns a value typically in [0.04, 0.20].
    """
    base = 0.10
    if sample_size >= 8:
        base -= 0.02
    elif sample_size <= 3:
        base += 0.04
    base += min(0.10, max(0.0, cv * 0.5))
    return round(min(0.20, max(0.04, base)), 3)


def _build_explanation(v: Valuation, comp_match, anchor) -> str:
    """Produce a human-readable explanation for the dashboard."""
    parts = []
    if v.raw_active_comp_median is not None:
        parts.append(
            f"raw active median £{v.raw_active_comp_median:.2f} "
            f"({v.active_comp_count} comps)"
        )
    if v.v1_expected_resale is not None:
        parts.append(f"v1 estimate £{v.v1_expected_resale:.2f}")
    if v.condition_reasons:
        parts.append("conditions: " + "; ".join(v.condition_reasons))
    if anchor:
        parts.append(
            f"anchor £{anchor.low:.0f}/£{anchor.mid:.0f}/£{anchor.high:.0f}"
        )
    parts.append(
        f"weights: {', '.join(f'{k}={v_w:.0%}' for k, v_w in v.source_weights.items())}"
    )
    parts.append(
        f"range: £{v.conservative_resale:.2f} / v2 £{v.expected_resale:.2f} "
        f"/ £{v.optimistic_resale:.2f}"
    )
    if v.warnings:
        parts.append("warnings: " + ", ".join(v.warnings))
    return " | ".join(parts)
