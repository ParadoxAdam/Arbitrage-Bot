"""
Condition + battery-health adjustments for valuation.

This wraps the v15.4.7 relative condition logic — it does NOT add a new
absolute condition discount on top. The flow:

  1. Comp-pool side: relative condition adjustment was already applied
     during _build_match_from_set; that produces a comp-anchored
     `expected_resale` for a target of equivalent condition.
  2. Battery-health adjustment: applied on top ONLY when the target has
     a stated low or unknown battery health AND the comp pool likely
     doesn't (we can't tell perfectly, so this discount is small).
  3. Functional-damage adjustment: stronger discount for soft-damage
     flags that don't fully kill the listing (cosmetic damage, dent).

Adjustments are returned as multiplicative factors so the engine can
compose them transparently and surface them in the explanation.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ConditionAdjustment:
    factor: float                       # multiplicative on expected_resale
    reasons: list[str]                  # human-readable adjustments applied
    confidence_penalty: float = 0.0     # additive penalty (0..1) on confidence


# Battery health bands and their multipliers
# (target battery state vs an assumed-healthy comp pool)
_BATTERY_FACTORS = {
    "healthy_or_new": 1.00,         # 90%+ stated, or condition=new/like_new
    "missing":        0.95,         # not stated, condition is used
    "moderate":       0.92,         # 80-89% stated
    "low":            0.85,         # 70-79% stated
    "very_low":       0.75,         # <70% stated
}


def _classify_battery(target_condition: str, risk_flags: list[str],
                       title: str) -> tuple[str, str | None]:
    """Return (band, explicit_pct_str_or_None)."""
    if target_condition in ("new", "like_new"):
        return "healthy_or_new", None

    title_l = (title or "").lower()

    # Try to extract an explicit % BH if present
    import re
    m = re.search(r"\b(\d{1,3})\s*%\s*(?:bh|battery|battery\s+health)?\b", title_l)
    explicit_pct = None
    if m:
        try:
            v = int(m.group(1))
            if 50 <= v <= 100:
                explicit_pct = v
        except ValueError:
            pass

    if explicit_pct is not None:
        if explicit_pct >= 90:
            return "healthy_or_new", f"{explicit_pct}%"
        if explicit_pct >= 80:
            return "moderate", f"{explicit_pct}%"
        if explicit_pct >= 70:
            return "low", f"{explicit_pct}%"
        return "very_low", f"{explicit_pct}%"

    # No explicit %
    if "missing_battery_health" in (risk_flags or []):
        return "missing", None
    return "healthy_or_new", None


def compute_condition_adjustment(
    target_condition: str,
    risk_flags: list[str],
    title: str,
    comp_pool_already_relative: bool = True,
) -> ConditionAdjustment:
    """
    Compute the multiplicative condition factor + confidence penalty.

    Caller passes `comp_pool_already_relative=True` (default) when the
    comp-based expected_resale was already produced by v15.4.7's relative
    condition logic. In that case we ONLY apply battery and functional-damage
    deltas here, never another full condition discount.
    """
    factor = 1.0
    reasons: list[str] = []
    conf_penalty = 0.0

    # ── Battery health (phones)
    band, pct = _classify_battery(target_condition, risk_flags, title)
    bf = _BATTERY_FACTORS[band]
    if bf < 1.0:
        factor *= bf
        if pct:
            reasons.append(f"battery {band} ({pct}) → ×{bf:.2f}")
        else:
            reasons.append(f"battery {band} → ×{bf:.2f}")
        if band == "missing":
            conf_penalty += 0.05    # small confidence ding

    # ── Functional damage signals (soft, since strong damage = critical)
    soft_damage_flags = {
        "possible_damage", "low_battery_health", "missing_battery_health",
    }
    soft_present = soft_damage_flags & set(risk_flags or [])
    # Already counted battery flags above; only apply additional discount
    # for "possible_damage" (cracks, cosmetic, repaired)
    if "possible_damage" in soft_present:
        factor *= 0.92
        reasons.append("possible damage signal → ×0.92")
        conf_penalty += 0.05

    # ── If we're NOT wrapping the v15.4.7 relative logic, fall back
    # to absolute condition values. This branch exists for completeness
    # but is not used in production.
    if not comp_pool_already_relative:
        from ..pricing.comps import CONDITION_VALUE
        cond_factor = CONDITION_VALUE.get(target_condition, 0.85)
        factor *= cond_factor
        reasons.append(f"absolute condition '{target_condition}' → ×{cond_factor:.2f}")

    return ConditionAdjustment(
        factor=round(factor, 4),
        reasons=reasons,
        confidence_penalty=round(conf_penalty, 3),
    )
