"""
Review candidate storage + structured decision workflow.

A candidate has two state fields:
  - decision: pending | approved | passed_no_action | watchlist |
              needs_more_info | rejected_*
  - lifecycle_stage: none | purchased | listed | sold | closed

Every decision change logs a ReviewDecisionEventRow for full audit trail.
"""
from __future__ import annotations
import logging
from typing import Optional
from .config import settings, CURRENT_ENGINE_VERSION
from .models import (
    Opportunity, ReviewCandidateRow, ReviewDecisionEventRow, _utcnow,
)
from .dedupe import exact_dedupe_key
from .db import session_scope
from .decisions import (
    PENDING, APPROVED, PASSED_NO_ACTION, WATCHLIST, NEEDS_MORE_INFO,
    ALL_DECISIONS, REJECTION_DECISIONS, REJECTED_MOCK,
    STAGE_NONE,
)

log = logging.getLogger("review")


# ── Candidate creation ──────────────────────────────────────────────

def _build_why(op: Opportunity) -> str:
    cur = settings.currency_symbol
    parts = [
        f"profit={cur}{op.net_profit:.2f}",
        f"roi={op.roi*100:.1f}%",
        f"score={op.score:.2f}",
        f"conf={op.confidence:.2f}",
        f"match={op.match_quality:.2f}",
        f"comps={op.comp_source}({op.comp_count})",
    ]
    if op.match_details:
        parts.append(f"match: {op.match_details}")
    return " | ".join(parts)


def _build_penalties(op: Opportunity) -> list[str]:
    penalties = []
    if op.comp_source == "active":
        penalties.append("active_listing_discount: 12-18% applied (not sold data)")
    if op.match_quality < 0.5:
        penalties.append(f"confidence_capped_40: weak match quality ({op.match_quality:.2f})")
    elif op.match_quality < 0.7 and op.comp_source == "active":
        penalties.append(f"confidence_capped_50: active comps + mediocre match ({op.match_quality:.2f})")
    if op.comp_count < 5:
        penalties.append(f"confidence_capped_55: small sample ({op.comp_count} comps)")
    if op.comp_count < 8 and op.comp_source == "active":
        penalties.append(f"confidence_capped_45: few active comps ({op.comp_count})")
    for flag in op.risk_flags:
        penalties.append(f"risk_flag: {flag}")
    return penalties


def store_review_candidate(
    op: Opportunity,
    listing_row_id: int | None = None,
    opportunity_id: int | None = None,
) -> int | None:
    """
    Create a review candidate row. Returns the new candidate ID,
    or the existing pending candidate's ID if a duplicate.
    """
    l = op.listing
    key = exact_dedupe_key(l)
    with session_scope() as s:
        existing = s.query(ReviewCandidateRow).filter_by(
            dedupe_key=key, decision=PENDING).first()
        if existing:
            return existing.id

        val = op.valuation or {}
        row = ReviewCandidateRow(
            listing_id=listing_row_id,
            title=l.title, source=l.source, source_url=l.source_url,
            brand=l.brand, model_name=l.model, category=l.category,
            condition=l.condition, price=l.price, shipping=l.shipping,
            fair_value=op.fair_value, expected_resale=op.expected_resale,
            net_profit=op.net_profit, roi=op.roi,
            confidence=op.confidence, liquidity=op.liquidity,
            score=op.score, risk_flags=op.risk_flags,
            comp_source=op.comp_source, comp_count=op.comp_count,
            match_quality=op.match_quality,
            match_details=op.match_details,
            comp_evidence=op.comp_evidence,
            needs_review=True, why_passed=_build_why(op),
            penalties_applied=_build_penalties(op),
            status="pending",
            decision=PENDING,
            lifecycle_stage=STAGE_NONE,
            is_mock=(l.source == "mock"),
            dedupe_key=key,
            engine_version=CURRENT_ENGINE_VERSION,
            # v15.5
            valuation_version=val.get("valuation_version"),
            valuation_method=val.get("valuation_method"),
            valuation_confidence=val.get("valuation_confidence"),
            conservative_resale=val.get("conservative_resale"),
            optimistic_resale=val.get("optimistic_resale"),
            valuation_warnings=val.get("warnings") or None,
            valuation_breakdown_json=val or None,
            # v15.5.1
            v1_expected_resale=val.get("v1_expected_resale"),
            v2_expected_resale=val.get("expected_resale"),
        )
        s.add(row)
        s.flush()
        candidate_id = row.id

    log.info("  >> REVIEW: %s  %s%.2f  match=%.2f  %s(%d)",
             l.title[:40], settings.currency_symbol, op.net_profit,
             op.match_quality, op.comp_source, op.comp_count)
    return candidate_id


# ── Decision workflow ───────────────────────────────────────────────

def set_decision(
    candidate_id: int,
    new_decision: str,
    *,
    notes: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> None:
    """
    Apply a decision to a candidate. Logs an event for the audit trail.
    Idempotent for same decision (still logs the event).
    """
    if new_decision not in ALL_DECISIONS:
        raise ValueError(
            f"Invalid decision '{new_decision}'. "
            f"Must be one of: {ALL_DECISIONS}"
        )

    with session_scope() as s:
        cand = s.query(ReviewCandidateRow).filter_by(id=candidate_id).first()
        if not cand:
            raise ValueError(f"Candidate {candidate_id} not found")

        previous = cand.decision
        cand.decision = new_decision
        cand.decision_notes = notes
        cand.reviewed_at = _utcnow()

        # Keep legacy `status` synced for backward compat
        if new_decision == APPROVED:
            cand.status = "approved"
        elif new_decision in REJECTION_DECISIONS:
            cand.status = "rejected"
        else:
            cand.status = "pending"

        # Watchlist toggle
        cand.watchlist = (new_decision == WATCHLIST)

        # Log event
        event = ReviewDecisionEventRow(
            candidate_id=candidate_id,
            previous_decision=previous,
            new_decision=new_decision,
            reason_code=reason_code or new_decision,
            notes=notes,
        )
        s.add(event)

    log.info("decision: candidate=%d %s -> %s", candidate_id, previous, new_decision)


def get_decision_history(candidate_id: int) -> list[dict]:
    """Return the full decision event history for a candidate."""
    with session_scope() as s:
        events = (s.query(ReviewDecisionEventRow)
                  .filter_by(candidate_id=candidate_id)
                  .order_by(ReviewDecisionEventRow.created_at.asc())
                  .all())
        return [
            {
                "id": e.id,
                "previous_decision": e.previous_decision,
                "new_decision": e.new_decision,
                "reason_code": e.reason_code,
                "notes": e.notes,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
