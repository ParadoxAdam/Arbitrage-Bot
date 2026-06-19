"""
Analytics over candidates, decisions, and trade outcomes.
Designed to surface patterns for tuning thresholds, filters, scoring.

v15.4: All analytics functions accept engine_version filter (defaults to
"current" for the latest version, set to None to include all data).
"""
from __future__ import annotations
from collections import Counter, defaultdict
from .config import settings, CURRENT_ENGINE_VERSION
from .db import session_scope
from .models import (
    ReviewCandidateRow, ReviewDecisionEventRow,
    PurchaseRecordRow, SaleRecordRow, PnlSnapshotRow,
)
from .decisions import (
    PENDING, APPROVED, PASSED_NO_ACTION, WATCHLIST, NEEDS_MORE_INFO,
    REJECTION_DECISIONS, ANALYTICS_EXCLUDED, REJECTED_MOCK,
    DECISION_LABELS,
)


def _resolve_engine_filter(engine_version: str | None):
    """
    Resolve the engine_version filter argument.
    "current" → current engine version
    "all" or None → no filter
    Anything else → exact match
    """
    if engine_version in (None, "all", ""):
        return None
    if engine_version == "current":
        return CURRENT_ENGINE_VERSION
    return engine_version


def candidate_summary(
    *, exclude_mock: bool = True, engine_version: str | None = "current",
) -> dict:
    """High-level counts across the candidate funnel."""
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = s.query(ReviewCandidateRow)
        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        cands = q.all()
        decisions = Counter(c.decision for c in cands)

        # Funnel counts
        total = len(cands)
        pending = decisions.get(PENDING, 0)
        approved = decisions.get(APPROVED, 0)
        passed = decisions.get(PASSED_NO_ACTION, 0)
        watchlist = sum(1 for c in cands if c.watchlist)
        rejected = sum(decisions.get(r, 0) for r in REJECTION_DECISIONS)

        # Lifecycle
        purchased = sum(1 for c in cands if c.lifecycle_stage == "purchased")
        listed = sum(1 for c in cands if c.lifecycle_stage == "listed")
        sold = sum(1 for c in cands if c.lifecycle_stage == "sold")
        closed = sum(1 for c in cands if c.lifecycle_stage == "closed")

        return {
            "total": total,
            "pending": pending,
            "approved": approved,
            "passed_no_action": passed,
            "watchlist": watchlist,
            "rejected": rejected,
            "lifecycle": {
                "purchased": purchased,
                "listed": listed,
                "sold": sold,
                "closed": closed,
            },
            "decisions_breakdown": dict(decisions),
            "exclude_mock": exclude_mock,
            "engine_version_filter": ev or "all",
        }


def rejection_patterns(*, exclude_mock: bool = True, engine_version: str | None = "current") -> dict:
    """Most common rejection reasons. Used to spot what the bot keeps getting wrong."""
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = s.query(ReviewCandidateRow)
        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)
        q = q.filter(ReviewCandidateRow.decision.in_(REJECTION_DECISIONS))

        rows = q.all()
        by_reason = Counter(r.decision for r in rows)
        by_category = defaultdict(Counter)
        by_source = defaultdict(Counter)

        for r in rows:
            by_category[r.category][r.decision] += 1
            by_source[r.source][r.decision] += 1

        return {
            "total_rejections": len(rows),
            "by_reason": [
                {
                    "reason": k,
                    "label": DECISION_LABELS.get(k, k),
                    "count": v,
                    "pct": round(100 * v / len(rows), 1) if rows else 0,
                }
                for k, v in by_reason.most_common()
            ],
            "by_category": {
                cat: dict(counts) for cat, counts in by_category.items()
            },
            "by_source": {
                src: dict(counts) for src, counts in by_source.items()
            },
        }


def category_performance(*, exclude_mock: bool = True, engine_version: str | None = "current") -> list[dict]:
    """
    Per-category breakdown:
      candidates -> approved -> bought -> sold -> avg actual ROI
    """
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = s.query(ReviewCandidateRow)
        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        cands = q.all()

        by_cat: dict[str, dict] = defaultdict(lambda: {
            "candidates": 0, "approved": 0, "rejected": 0,
            "purchased": 0, "sold": 0,
            "actual_profits": [], "actual_rois": [],
        })

        cand_ids_by_cat = defaultdict(list)
        for c in cands:
            d = by_cat[c.category]
            d["candidates"] += 1
            if c.decision == APPROVED:
                d["approved"] += 1
            if c.decision in REJECTION_DECISIONS:
                d["rejected"] += 1
            if c.lifecycle_stage in ("purchased", "listed", "sold", "closed"):
                d["purchased"] += 1
            if c.lifecycle_stage == "sold":
                d["sold"] += 1
            cand_ids_by_cat[c.category].append(c.id)

        # Pull in P&L for sold items per category
        for cat, cand_ids in cand_ids_by_cat.items():
            purchases = (s.query(PurchaseRecordRow)
                         .filter(PurchaseRecordRow.candidate_id.in_(cand_ids))
                         .all())
            for p in purchases:
                snap = s.query(PnlSnapshotRow).filter_by(
                    purchase_id=p.id).first()
                if snap and snap.is_finalized:
                    by_cat[cat]["actual_profits"].append(snap.actual_net_profit)
                    by_cat[cat]["actual_rois"].append(snap.actual_roi)

        out = []
        for cat, d in by_cat.items():
            avg_profit = (sum(d["actual_profits"]) / len(d["actual_profits"])
                          if d["actual_profits"] else None)
            avg_roi = (sum(d["actual_rois"]) / len(d["actual_rois"])
                       if d["actual_rois"] else None)
            out.append({
                "category": cat,
                "candidates": d["candidates"],
                "approved": d["approved"],
                "rejected": d["rejected"],
                "purchased": d["purchased"],
                "sold": d["sold"],
                "approval_rate": (d["approved"] / d["candidates"]
                                  if d["candidates"] else 0),
                "purchase_rate": (d["purchased"] / d["approved"]
                                  if d["approved"] else 0),
                "sell_rate": (d["sold"] / d["purchased"]
                              if d["purchased"] else 0),
                "avg_actual_profit": (round(avg_profit, 2)
                                      if avg_profit is not None else None),
                "avg_actual_roi": (round(avg_roi, 4)
                                   if avg_roi is not None else None),
            })
        return out


def source_performance(*, exclude_mock: bool = True, engine_version: str | None = "current") -> list[dict]:
    """Per-source approval/rejection breakdown."""
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = s.query(ReviewCandidateRow)
        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)
        cands = q.all()

        by_source: dict[str, dict] = defaultdict(
            lambda: {"candidates": 0, "approved": 0, "rejected": 0, "sold": 0}
        )
        for c in cands:
            d = by_source[c.source]
            d["candidates"] += 1
            if c.decision == APPROVED:
                d["approved"] += 1
            if c.decision in REJECTION_DECISIONS:
                d["rejected"] += 1
            if c.lifecycle_stage == "sold":
                d["sold"] += 1

        return [{"source": k, **v} for k, v in by_source.items()]


def predicted_vs_actual(*, exclude_mock: bool = True, engine_version: str | None = "current") -> dict:
    """
    Mean error metrics across finalized P&L snapshots.
    Surfaces where the bot's predictions are systematically off.
    """
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = (s.query(PnlSnapshotRow, ReviewCandidateRow)
             .join(PurchaseRecordRow,
                   PnlSnapshotRow.purchase_id == PurchaseRecordRow.id)
             .join(ReviewCandidateRow,
                   PurchaseRecordRow.candidate_id == ReviewCandidateRow.id)
             .filter(PnlSnapshotRow.is_finalized == True))  # noqa: E712

        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        rows = q.all()
        n = len(rows)
        if n == 0:
            return {"sample_size": 0}

        snaps = [r[0] for r in rows]
        return {
            "sample_size": n,
            "avg_resale_error": round(sum(s.resale_error for s in snaps) / n, 2),
            "avg_profit_error": round(sum(s.profit_error for s in snaps) / n, 2),
            "avg_roi_error": round(sum(s.roi_error for s in snaps) / n, 4),
            "avg_predicted_resale": round(sum(s.predicted_resale for s in snaps) / n, 2),
            "avg_actual_resale": round(sum(s.actual_gross_proceeds for s in snaps) / n, 2),
            "overestimate_count": sum(1 for s in snaps if s.profit_error < 0),
            "underestimate_count": sum(1 for s in snaps if s.profit_error > 0),
        }


def confidence_calibration(*, exclude_mock: bool = True, engine_version: str | None = "current") -> list[dict]:
    """
    Bucket predictions by confidence band and report actual win rate per band.
    Tells us whether confidence numbers are honest signals.
    """
    BANDS = [(0.0, 0.3, "very_low"), (0.3, 0.5, "low"),
             (0.5, 0.7, "mid"), (0.7, 0.85, "high"), (0.85, 1.01, "very_high")]
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = (s.query(PurchaseRecordRow, PnlSnapshotRow, ReviewCandidateRow)
             .join(PnlSnapshotRow,
                   PnlSnapshotRow.purchase_id == PurchaseRecordRow.id)
             .join(ReviewCandidateRow,
                   PurchaseRecordRow.candidate_id == ReviewCandidateRow.id)
             .filter(PnlSnapshotRow.is_finalized == True))  # noqa: E712

        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        rows = q.all()

    out = []
    for lo, hi, label in BANDS:
        in_band = [
            (p, snap) for (p, snap, _c) in rows
            if lo <= p.predicted_confidence < hi
        ]
        if not in_band:
            out.append({"band": label, "range": [lo, hi], "count": 0})
            continue
        wins = sum(1 for _, snap in in_band if snap.actual_net_profit > 0)
        out.append({
            "band": label,
            "range": [lo, hi],
            "count": len(in_band),
            "win_rate": round(wins / len(in_band), 3),
            "avg_profit": round(
                sum(snap.actual_net_profit for _, snap in in_band) / len(in_band), 2
            ),
        })
    return out


def biggest_misses(limit: int = 10, *, exclude_mock: bool = True,
                   engine_version: str | None = "current") -> list[dict]:
    """Purchases where predicted profit was high but actual was poor."""
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = (s.query(PnlSnapshotRow, PurchaseRecordRow, ReviewCandidateRow)
             .join(PurchaseRecordRow,
                   PnlSnapshotRow.purchase_id == PurchaseRecordRow.id)
             .join(ReviewCandidateRow,
                   PurchaseRecordRow.candidate_id == ReviewCandidateRow.id)
             .filter(PnlSnapshotRow.is_finalized == True))  # noqa: E712

        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        rows = q.order_by(PnlSnapshotRow.profit_error.asc()).limit(limit).all()

        return [
            {
                "candidate_id": cand.id,
                "title": cand.title,
                "predicted_profit": snap.predicted_profit,
                "actual_profit": snap.actual_net_profit,
                "profit_error": snap.profit_error,
                "predicted_confidence": purchase.predicted_confidence,
            }
            for snap, purchase, cand in rows
        ]


def top_wins(limit: int = 10, *, exclude_mock: bool = True,
             engine_version: str | None = "current") -> list[dict]:
    """Purchases where actual profit was highest."""
    ev = _resolve_engine_filter(engine_version)
    with session_scope() as s:
        q = (s.query(PnlSnapshotRow, PurchaseRecordRow, ReviewCandidateRow)
             .join(PurchaseRecordRow,
                   PnlSnapshotRow.purchase_id == PurchaseRecordRow.id)
             .join(ReviewCandidateRow,
                   PurchaseRecordRow.candidate_id == ReviewCandidateRow.id)
             .filter(PnlSnapshotRow.is_finalized == True))  # noqa: E712

        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        rows = q.order_by(PnlSnapshotRow.actual_net_profit.desc()).limit(limit).all()

        return [
            {
                "candidate_id": cand.id,
                "title": cand.title,
                "predicted_profit": snap.predicted_profit,
                "actual_profit": snap.actual_net_profit,
                "actual_roi": snap.actual_roi,
            }
            for snap, _purchase, cand in rows
        ]
