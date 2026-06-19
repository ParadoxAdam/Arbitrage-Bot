"""
Entry point.
  Auto-scanner:   python -m app.main
  One-shot:       python -m app.main --once
  API server:     uvicorn app.main:app --reload
"""
import sys
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import or_, and_

from .config import settings
from .db import init_db, session_scope
from .models import (
    ReviewCandidateRow, ReviewDecisionEventRow,
    PurchaseRecordRow, SaleRecordRow, PnlSnapshotRow, ScanRunRow,
)
from .pipeline import run_once
from .scheduler import start_scheduler, stop_scheduler
from .review import set_decision, get_decision_history
from .trades import (
    record_purchase, update_purchase,
    record_sale_listing, record_sale_completed, record_sale_closed,
)
from .pnl import get_pnl_summary
from .analytics import (
    candidate_summary, rejection_patterns, category_performance,
    source_performance, predicted_vs_actual, confidence_calibration,
    biggest_misses, top_wins,
)
from .decisions import (
    PENDING, APPROVED, REJECTION_DECISIONS, ALL_DECISIONS,
    SALE_LISTED, SALE_SOLD, SALE_RETURNED, SALE_ABANDONED,
    SALE_UNSOLD_HOLDING, SALE_RELISTED, SALE_LIQUIDATED,
    SALE_WRITTEN_OFF, ALL_SALE_STATUSES,
)
from .dashboard import DASHBOARD_HTML

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = start_scheduler(interval_minutes=settings.scan_interval_minutes)
    yield
    stop_scheduler()


app = FastAPI(title="Resale Arbitrage Bot", lifespan=lifespan)


# ── Dashboard ───────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML.replace("__CUR__", settings.currency_symbol)


# ── Health & scan ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True, "env": settings.env}


@app.post("/scan")
def trigger_scan():
    n = run_once()
    return {"alerts_sent": n}


# ── Review listing / filtering ──────────────────────────────────────

def _candidate_negotiation_block(r: ReviewCandidateRow) -> dict | None:
    """
    Compute the v15.5.9 negotiation block for a review candidate.
    Uses the same expected_resale that drove the original profit math
    (i.e. v2 if `use_v2_for_profit` was on at scoring time, else v1).
    Returns None for mock rows or when expected_resale is missing.
    """
    if r.is_mock or not r.expected_resale or r.expected_resale <= 0:
        return None
    try:
        from .pricing.negotiation import analyze
        return analyze(
            price=r.price,
            shipping=r.shipping or 0.0,
            expected_resale=r.expected_resale,
            failure_reasons=[],          # candidates passed review by definition
            risk_flags=r.risk_flags or [],
            valuation_confidence=r.valuation_confidence,
            valuation_warnings=r.valuation_warnings or [],
        )
    except Exception:
        # Negotiation is purely additive; never break the API on its account.
        return None


def _serialize_candidate(r: ReviewCandidateRow) -> dict:
    return {
        "id": r.id, "title": r.title, "source": r.source,
        "source_url": r.source_url, "brand": r.brand,
        "model": r.model_name, "category": r.category,
        "condition": r.condition, "price": r.price,
        "shipping": r.shipping, "expected_resale": r.expected_resale,
        "net_profit": r.net_profit, "roi": f"{r.roi*100:.1f}%",
        "score": r.score, "confidence": r.confidence,
        "match_quality": r.match_quality, "match_details": r.match_details,
        "comp_source": r.comp_source, "comp_count": r.comp_count,
        "comp_evidence": r.comp_evidence,
        "risk_flags": r.risk_flags, "why_passed": r.why_passed,
        "penalties_applied": r.penalties_applied,
        "decision": r.decision, "decision_notes": r.decision_notes,
        "lifecycle_stage": r.lifecycle_stage,
        "watchlist": r.watchlist, "is_mock": r.is_mock,
        "status": r.status,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        # v15.5 — Valuation Engine v2
        "valuation_version": r.valuation_version,
        "valuation_method": r.valuation_method,
        "valuation_confidence": r.valuation_confidence,
        "conservative_resale": r.conservative_resale,
        "optimistic_resale": r.optimistic_resale,
        "valuation_warnings": r.valuation_warnings,
        "valuation_breakdown": r.valuation_breakdown_json,
        # v15.5.1 — explicit v1 / v2 estimates so the dashboard can show both
        "v1_expected_resale": r.v1_expected_resale,
        "v2_expected_resale": r.v2_expected_resale,
        # v15.5.9 — target buy price / negotiation analysis
        "negotiation": _candidate_negotiation_block(r),
    }


@app.get("/review")
def list_candidates(
    decision: Optional[str] = None,
    decision_group: Optional[str] = None,
    lifecycle_stage: Optional[str] = None,
    watchlist: Optional[bool] = None,
    include_mock: bool = False,
    limit: int = 100,
):
    """
    Filtered candidate list.
    - decision: comma-separated list of decisions
    - decision_group: 'rejected' (matches all rejection reasons)
    - lifecycle_stage: comma-separated list
    - watchlist: true/false
    """
    with session_scope() as s:
        q = s.query(ReviewCandidateRow)

        if not include_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712

        if decision:
            decisions = [d.strip() for d in decision.split(",")]
            q = q.filter(ReviewCandidateRow.decision.in_(decisions))

        if decision_group == "rejected":
            q = q.filter(ReviewCandidateRow.decision.in_(REJECTION_DECISIONS))

        if lifecycle_stage:
            stages = [s.strip() for s in lifecycle_stage.split(",")]
            q = q.filter(ReviewCandidateRow.lifecycle_stage.in_(stages))

        if watchlist is not None:
            q = q.filter(ReviewCandidateRow.watchlist == watchlist)

        q = q.order_by(ReviewCandidateRow.score.desc()).limit(limit)
        return [_serialize_candidate(r) for r in q.all()]


@app.get("/review/{cid}")
def get_candidate(cid: int):
    with session_scope() as s:
        r = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        if not r:
            raise HTTPException(404, "Candidate not found")
        from .trades import get_lifecycle_history
        return {
            **_serialize_candidate(r),
            "decision_history": get_decision_history(cid),
            "lifecycle_history": get_lifecycle_history(cid),
        }


# ── Decision actions ────────────────────────────────────────────────

class DecideBody(BaseModel):
    decision: str
    notes: Optional[str] = None
    reason_code: Optional[str] = None


@app.post("/review/{cid}/decide")
def decide(cid: int, body: DecideBody):
    if body.decision not in ALL_DECISIONS:
        raise HTTPException(400, f"Invalid decision: {body.decision}")
    try:
        set_decision(
            cid, body.decision,
            notes=body.notes, reason_code=body.reason_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "candidate_id": cid, "decision": body.decision}


# Backward-compat endpoints kept so old scripts don't break
@app.post("/review/{cid}/approve")
def approve(cid: int):
    set_decision(cid, APPROVED)
    return {"ok": True, "decision": APPROVED}


@app.post("/review/{cid}/reject")
def reject(cid: int):
    set_decision(cid, "rejected_other")
    return {"ok": True, "decision": "rejected_other"}


# ── Purchase tracking ───────────────────────────────────────────────

class PurchaseBody(BaseModel):
    actual_purchase_price: float
    purchased_at: Optional[datetime] = None  # manual date for backfill
    tax_paid: float = 0.0
    inbound_shipping_cost: float = 0.0
    repair_cost: float = 0.0
    misc_buy_costs: float = 0.0
    marketplace_purchased_from: Optional[str] = None
    purchase_url: Optional[str] = None
    purchase_notes: Optional[str] = None
    seller_risk_notes: Optional[str] = None


@app.post("/review/{cid}/purchase")
def post_purchase(cid: int, body: PurchaseBody):
    try:
        purchase_id = record_purchase(cid, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "purchase_id": purchase_id}


@app.get("/review/{cid}/purchase")
def get_candidate_purchase(cid: int):
    """Return purchase record for a candidate (if any)."""
    with session_scope() as s:
        p = s.query(PurchaseRecordRow).filter_by(candidate_id=cid).first()
        if not p:
            return {"purchase_id": None}
        return _serialize_purchase(p)


def _serialize_purchase(p: PurchaseRecordRow) -> dict:
    return {
        "purchase_id": p.id,
        "candidate_id": p.candidate_id,
        "purchased_at": p.purchased_at.isoformat() if p.purchased_at else None,
        "actual_purchase_price": p.actual_purchase_price,
        "tax_paid": p.tax_paid,
        "inbound_shipping_cost": p.inbound_shipping_cost,
        "repair_cost": p.repair_cost,
        "misc_buy_costs": p.misc_buy_costs,
        "marketplace_purchased_from": p.marketplace_purchased_from,
        "purchase_url": p.purchase_url,
        "purchase_notes": p.purchase_notes,
        "seller_risk_notes": p.seller_risk_notes,
        "predicted_resale": p.predicted_resale,
        "predicted_profit": p.predicted_profit,
        "predicted_roi": p.predicted_roi,
        "predicted_confidence": p.predicted_confidence,
    }


# ── Sale tracking ───────────────────────────────────────────────────

class SaleBody(BaseModel):
    sale_status: str
    actual_sale_price: float = 0.0
    outbound_shipping_cost: float = 0.0
    selling_fees: float = 0.0
    payment_processing_fees: float = 0.0
    return_costs: float = 0.0
    sale_platform: Optional[str] = None
    final_notes: Optional[str] = None
    listed_at: Optional[datetime] = None     # manual override
    sale_date: Optional[datetime] = None     # manual override
    relist: bool = False                     # for sale_status=listed only


@app.post("/purchase/{purchase_id}/sale")
def post_sale(purchase_id: int, body: SaleBody):
    from .decisions import (
        SALE_LISTED, SALE_RELISTED, SALE_SOLD, SALE_LIQUIDATED,
        SALE_UNSOLD_HOLDING, SALE_RETURNED, SALE_WRITTEN_OFF, SALE_ABANDONED,
    )
    from .trades import record_sale_unsold_holding
    try:
        if body.sale_status in (SALE_SOLD, SALE_LIQUIDATED):
            sale_id = record_sale_completed(
                purchase_id,
                actual_sale_price=body.actual_sale_price,
                sale_date=body.sale_date,
                listed_at=body.listed_at,
                outbound_shipping_cost=body.outbound_shipping_cost,
                selling_fees=body.selling_fees,
                payment_processing_fees=body.payment_processing_fees,
                return_costs=body.return_costs,
                sale_platform=body.sale_platform,
                final_notes=body.final_notes,
                sold_via=body.sale_status,
            )
        elif body.sale_status in (SALE_LISTED, SALE_RELISTED):
            sale_id = record_sale_listing(
                purchase_id, sale_platform=body.sale_platform,
                listed_at=body.listed_at,
                relist=(body.sale_status == SALE_RELISTED or body.relist),
            )
        elif body.sale_status == SALE_UNSOLD_HOLDING:
            sale_id = record_sale_unsold_holding(
                purchase_id, notes=body.final_notes,
            )
        elif body.sale_status in (SALE_RETURNED, SALE_WRITTEN_OFF, SALE_ABANDONED):
            sale_id = record_sale_closed(
                purchase_id, final_status=body.sale_status,
                return_costs=body.return_costs,
                final_notes=body.final_notes,
            )
        else:
            raise HTTPException(400, f"Invalid sale_status: {body.sale_status}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "sale_id": sale_id}


# ── Analytics ───────────────────────────────────────────────────────

@app.get("/analytics/candidates")
def analytics_candidates(include_mock: bool = False,
                          engine_version: str = "current"):
    return candidate_summary(exclude_mock=not include_mock,
                             engine_version=engine_version)


@app.get("/analytics/rejections")
def analytics_rejections(include_mock: bool = False,
                          engine_version: str = "current"):
    return rejection_patterns(exclude_mock=not include_mock,
                              engine_version=engine_version)


@app.get("/analytics/categories")
def analytics_categories(include_mock: bool = False,
                          engine_version: str = "current"):
    return category_performance(exclude_mock=not include_mock,
                                engine_version=engine_version)


@app.get("/analytics/sources")
def analytics_sources(include_mock: bool = False,
                      engine_version: str = "current"):
    return source_performance(exclude_mock=not include_mock,
                              engine_version=engine_version)


@app.get("/analytics/pnl")
def analytics_pnl(include_mock: bool = False,
                  engine_version: str = "current"):
    return get_pnl_summary(exclude_mock=not include_mock,
                           engine_version=engine_version)


@app.get("/analytics/predicted-vs-actual")
def analytics_pva(include_mock: bool = False,
                  engine_version: str = "current"):
    return predicted_vs_actual(exclude_mock=not include_mock,
                               engine_version=engine_version)


@app.get("/analytics/confidence-calibration")
def analytics_conf(include_mock: bool = False,
                   engine_version: str = "current"):
    return confidence_calibration(exclude_mock=not include_mock,
                                  engine_version=engine_version)


@app.get("/analytics/top-wins")
def analytics_wins(limit: int = 10, include_mock: bool = False,
                   engine_version: str = "current"):
    return top_wins(limit=limit, exclude_mock=not include_mock,
                    engine_version=engine_version)


@app.get("/analytics/biggest-misses")
def analytics_misses(limit: int = 10, include_mock: bool = False,
                     engine_version: str = "current"):
    return biggest_misses(limit=limit, exclude_mock=not include_mock,
                          engine_version=engine_version)


@app.get("/analytics/engine-versions")
def list_engine_versions():
    """Return all engine versions present in the DB and the current one."""
    from sqlalchemy import distinct
    from .config import CURRENT_ENGINE_VERSION
    versions: set[str] = set()
    with session_scope() as s:
        for table_col in [
            (ScanRunRow, ScanRunRow.engine_version),
            (ReviewCandidateRow, ReviewCandidateRow.engine_version),
        ]:
            for (v,) in s.query(distinct(table_col[1])).all():
                if v:
                    versions.add(v)
    return {
        "current": CURRENT_ENGINE_VERSION,
        "all_seen": sorted(versions),
    }


# ── Scans ──────────────────────────────────────────────────────────

@app.get("/scans")
def list_scans(limit: int = 10):
    with session_scope() as s:
        rows = (s.query(ScanRunRow)
                .order_by(ScanRunRow.id.desc()).limit(limit).all())
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "sources": r.sources_used,
                "queries": r.queries_run,
                "listings": r.listings_found,
                "candidates": r.candidates_found,
                "alerts": r.alerts_sent,
                "status": r.status,
            }
            for r in rows
        ]


@app.get("/queries/performance")
def query_performance(limit: int = 200, engine_version: str = "current"):
    """
    Per-query performance summary. Aggregates across scan runs filtered
    by engine_version (default: current).
    """
    from .models import QueryPerformanceRow
    from .config import CURRENT_ENGINE_VERSION
    from sqlalchemy import func

    if engine_version in (None, "all", ""):
        ev = None
    elif engine_version == "current":
        ev = CURRENT_ENGINE_VERSION
    else:
        ev = engine_version

    with session_scope() as s:
        q = s.query(
            QueryPerformanceRow.query_terms,
            QueryPerformanceRow.category,
            func.count(QueryPerformanceRow.id).label("scan_count"),
            func.sum(QueryPerformanceRow.raw_returned).label("raw_returned"),
            func.sum(QueryPerformanceRow.negative_filtered).label("neg_filtered"),
            func.sum(QueryPerformanceRow.listings_fetched).label("fetched"),
            func.sum(QueryPerformanceRow.new_listings).label("new_listings"),
            func.sum(QueryPerformanceRow.duplicates_skipped).label("dupes"),
            func.sum(QueryPerformanceRow.listings_scored).label("scored"),
            func.sum(QueryPerformanceRow.exact_match_total).label("exact"),
            func.sum(QueryPerformanceRow.partial_match_total).label("partial"),
            func.sum(QueryPerformanceRow.broad_rejected_total).label("broad"),
            func.sum(QueryPerformanceRow.candidates_created).label("candidates"),
            func.sum(QueryPerformanceRow.alerts_sent).label("alerts"),
            func.sum(QueryPerformanceRow.failed_profit).label("f_profit"),
            func.sum(QueryPerformanceRow.failed_roi).label("f_roi"),
            func.sum(QueryPerformanceRow.failed_score).label("f_score"),
            func.sum(QueryPerformanceRow.failed_confidence).label("f_conf"),
            func.sum(QueryPerformanceRow.failed_match_quality).label("f_match"),
            func.sum(QueryPerformanceRow.failed_active_only).label("f_active"),
            func.sum(QueryPerformanceRow.failed_battery_health).label("f_battery"),
            func.sum(QueryPerformanceRow.failed_risk_flags).label("f_risk"),
            func.sum(QueryPerformanceRow.failed_comp_pool).label("f_comp_pool"),
            func.sum(QueryPerformanceRow.failed_no_comps).label("f_no_comps"),
            func.sum(QueryPerformanceRow.failed_other).label("f_other"),
        )
        if ev:
            q = q.filter(QueryPerformanceRow.engine_version == ev)
        rows = (
            q.group_by(QueryPerformanceRow.query_terms,
                       QueryPerformanceRow.category)
            .order_by(func.sum(QueryPerformanceRow.candidates_created).desc(),
                      func.sum(QueryPerformanceRow.new_listings).desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "query_terms": r.query_terms,
                "category": r.category,
                "scan_count": r.scan_count or 0,
                "raw_returned": r.raw_returned or 0,
                "negative_filtered": r.neg_filtered or 0,
                "fetched": r.fetched or 0,
                "new_listings": r.new_listings or 0,
                "duplicates": r.dupes or 0,
                "scored": r.scored or 0,
                "exact_matches": r.exact or 0,
                "partial_matches": r.partial or 0,
                "broad_rejected": r.broad or 0,
                "candidates": r.candidates or 0,
                "alerts": r.alerts or 0,
                # v15.4 — failure reason breakdown
                "failed_profit": r.f_profit or 0,
                "failed_roi": r.f_roi or 0,
                "failed_score": r.f_score or 0,
                "failed_confidence": r.f_conf or 0,
                "failed_match_quality": r.f_match or 0,
                "failed_active_only": r.f_active or 0,
                "failed_battery_health": r.f_battery or 0,
                "failed_risk_flags": r.f_risk or 0,
                "failed_comp_pool": r.f_comp_pool or 0,
                "failed_no_comps": r.f_no_comps or 0,
                "failed_other": r.f_other or 0,
            }
            for r in rows
        ]


@app.get("/queries/performance/{scan_run_id}")
def query_performance_for_scan(scan_run_id: int):
    """Per-query breakdown for a specific scan run."""
    from .models import QueryPerformanceRow
    with session_scope() as s:
        rows = (s.query(QueryPerformanceRow)
                .filter_by(scan_run_id=scan_run_id)
                .order_by(QueryPerformanceRow.candidates_created.desc(),
                          QueryPerformanceRow.new_listings.desc())
                .all())
        return [
            {
                "query_terms": r.query_terms,
                "category": r.category,
                "raw_returned": r.raw_returned,
                "negative_filtered": r.negative_filtered,
                "fetched": r.listings_fetched,
                "new_listings": r.new_listings,
                "duplicates": r.duplicates_skipped,
                "scored": r.listings_scored,
                "exact_matches": r.exact_match_total,
                "partial_matches": r.partial_match_total,
                "broad_rejected": r.broad_rejected_total,
                "candidates": r.candidates_created,
                "alerts": r.alerts_sent,
            }
            for r in rows
        ]


@app.get("/near-misses")
def near_misses(limit: int = 20):
    """
    Top near-misses from the most recent scan — listings that scored
    but didn't pass review thresholds. Reset every scan.

    v15.5.9: each row is enriched with a `negotiation` block that
    contains the target buy prices for review and alert thresholds,
    discount needed, and the failure-bucket flags.
    """
    from .pricing.comps import get_near_misses
    from .pricing.negotiation import analyze
    rows = []
    for m in get_near_misses(limit=limit):
        d = m.to_dict()
        try:
            if d.get("expected_resale") and d["expected_resale"] > 0:
                d["negotiation"] = analyze(
                    price=d["price"],
                    shipping=d.get("shipping") or 0.0,
                    expected_resale=d["expected_resale"],
                    failure_reasons=d.get("failure_reasons") or [],
                    risk_flags=d.get("risk_flags") or [],
                    valuation_confidence=d.get("valuation_confidence"),
                    valuation_warnings=d.get("valuation_warnings") or [],
                )
            else:
                d["negotiation"] = None
        except Exception:
            # Negotiation is purely additive — never fail this endpoint
            # because of it.
            d["negotiation"] = None
        rows.append(d)
    return rows


@app.get("/analytics/negotiation")
def analytics_negotiation(
    max_discount_pct: Optional[float] = None,
    max_discount_abs: Optional[float] = None,
):
    """
    Summary buckets for the current Top Failed pool (v15.5.9).

    Returns counts per bucket plus a list of listing snapshots that
    fall into each bucket, so the dashboard can show "X listings are
    profitable before fees", "Y listings are negotiable within £30",
    etc. — without re-running the math client-side.

    Parameters override the configured negotiation discount limits
    (defaults: settings.negotiation_max_discount_pct / _abs).
    """
    from .pricing.comps import get_near_misses
    from .pricing.negotiation import (
        target_buy_price, categorize_failure, ALL_BUCKETS,
        NEG_NEGOTIABLE,
    )

    if max_discount_pct is None:
        max_discount_pct = settings.negotiation_max_discount_pct
    if max_discount_abs is None:
        max_discount_abs = settings.negotiation_max_discount_abs

    items_by_bucket: dict[str, list[dict]] = {b: [] for b in ALL_BUCKETS}
    counts: dict[str, int] = {b: 0 for b in ALL_BUCKETS}
    total = 0
    by_label = {"already_passes": 0, "negotiable": 0,
                "too_expensive": 0, "infeasible": 0}

    # Pull a generous sample so the summary reflects the whole pool —
    # the underlying list is naturally capped by the in-memory near_misses.
    for m in get_near_misses(limit=10_000):
        d = m.to_dict()
        if not d.get("expected_resale") or d["expected_resale"] <= 0:
            continue
        total += 1
        try:
            review = target_buy_price(
                price=d["price"],
                shipping=d.get("shipping") or 0.0,
                expected_resale=d["expected_resale"],
                min_profit=settings.review_min_profit,
                min_roi=settings.review_min_roi,
                negotiable_max_pct=max_discount_pct,
                negotiable_max_abs=max_discount_abs,
            )
            alert = target_buy_price(
                price=d["price"],
                shipping=d.get("shipping") or 0.0,
                expected_resale=d["expected_resale"],
                min_profit=settings.min_profit,
                min_roi=settings.min_roi,
                negotiable_max_pct=max_discount_pct,
                negotiable_max_abs=max_discount_abs,
            )
            buckets = categorize_failure(
                failure_reasons=d.get("failure_reasons") or [],
                risk_flags=d.get("risk_flags") or [],
                price=d["price"],
                shipping=d.get("shipping") or 0.0,
                expected_resale=d["expected_resale"],
                valuation_confidence=d.get("valuation_confidence"),
                valuation_warnings=d.get("valuation_warnings") or [],
                target_review=review,
                target_alert=alert,
            )
        except Exception:
            continue

        if review.label in by_label:
            by_label[review.label] += 1

        snapshot = {
            "title": d.get("title"),
            "url": d.get("url"),
            "price": d.get("price"),
            "shipping": d.get("shipping"),
            "expected_resale": d.get("expected_resale"),
            "current_net_profit": review.current_net_profit,
            "current_roi": review.current_roi,
            "max_buy_for_review": review.max_buy_overall,
            "max_buy_for_alert": alert.max_buy_overall,
            "discount_needed_abs": review.discount_needed_abs,
            "discount_needed_pct": review.discount_needed_pct,
            "label": review.label,
        }
        for bkey, on in buckets.items():
            if on:
                counts[bkey] += 1
                items_by_bucket[bkey].append(snapshot)

    return {
        "total_failed_scored": total,
        "thresholds": {
            "review_min_profit": settings.review_min_profit,
            "review_min_roi": settings.review_min_roi,
            "alert_min_profit": settings.min_profit,
            "alert_min_roi": settings.min_roi,
            "max_discount_pct": max_discount_pct,
            "max_discount_abs": max_discount_abs,
        },
        "by_negotiation_label": by_label,
        "bucket_counts": counts,
        "bucket_items": items_by_bucket,
    }


# ── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    if "--once" in sys.argv:
        print("\n--- Running one-shot scan ---\n")
        n = run_once()
        print(f"\n--- Done. Alerts sent: {n} ---")
    else:
        interval = settings.scan_interval_minutes
        print(f"\n{'='*60}")
        print(f"  Arbitrage Bot — scanning every {interval} minutes")
        print(f"  Mode: REVIEW (shadow mode)")
        print(f"  Alerts -> {'Telegram (sold comps only)' if settings.telegram_bot_token else 'console'}")
        print(f"  eBay -> {'connected' if settings.ebay_client_id else 'not configured'}")
        print(f"  Press Ctrl+C to stop")
        print(f"{'='*60}\n")
        sched = start_scheduler(interval_minutes=interval)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n--- Shutting down ---")
            stop_scheduler()
            print("--- Stopped ---")
