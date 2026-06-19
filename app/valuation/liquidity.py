"""
Liquidity / sell-speed signal.

Outputs one of: "high" / "medium" / "low" / "unknown".
This is informational only — never creates a candidate, never inflates
confidence above what comp evidence supports. We surface it on the
dashboard so the operator can factor sell-speed into their decision.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class LiquiditySignal:
    band: str           # "high" | "medium" | "low" | "unknown"
    score: float        # 0..1
    reasons: list[str]


def compute_liquidity(
    *,
    exact_comp_count: int,
    partial_comp_count: int,
    raw_returned: int,
    spread_cv: float,           # coefficient of variation of comp prices
) -> LiquiditySignal:
    """
    Heuristic liquidity score.

      - More exact comps + tight spread + lots of raw results = high
      - Few exact comps OR wide spread = low
      - Anything in between = medium
    """
    reasons: list[str] = []
    score = 0.0

    # Exact-comp count drives most of the signal
    if exact_comp_count >= 8:
        score += 0.5
        reasons.append(f"{exact_comp_count} exact comps")
    elif exact_comp_count >= 4:
        score += 0.3
        reasons.append(f"{exact_comp_count} exact comps")
    elif exact_comp_count >= 2:
        score += 0.15
        reasons.append(f"only {exact_comp_count} exact comps")
    else:
        reasons.append("few/no exact comps")

    # Tight spread = stable market
    if spread_cv < 0.10:
        score += 0.25
        reasons.append("tight price spread")
    elif spread_cv < 0.20:
        score += 0.15
        reasons.append("moderate price spread")
    else:
        reasons.append("wide price spread")

    # Many raw eBay results = liquid product (lots of supply on market)
    if raw_returned >= 30:
        score += 0.2
        reasons.append(f"{raw_returned} raw matches available")
    elif raw_returned >= 15:
        score += 0.1
        reasons.append(f"{raw_returned} raw matches")

    # Partial comps as a tiebreaker
    if partial_comp_count >= 5:
        score += 0.05

    score = min(1.0, score)

    if exact_comp_count == 0 and partial_comp_count == 0:
        band = "unknown"
    elif score >= 0.65:
        band = "high"
    elif score >= 0.35:
        band = "medium"
    else:
        band = "low"

    return LiquiditySignal(band=band, score=round(score, 3), reasons=reasons)
