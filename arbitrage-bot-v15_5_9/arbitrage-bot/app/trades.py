"""
Trade execution layer: purchases, sales, lifecycle events.

Key principles:
  - All transitions log a LifecycleEventRow for audit
  - Manual date overrides supported (for backfilling old trades)
  - Sale statuses are nuanced — unsold-still-holding ≠ written-off
  - Only finalized statuses count toward P&L
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone as _tz
from typing import Optional
from .db import session_scope
from .models import (
    ReviewCandidateRow, PurchaseRecordRow, SaleRecordRow,
    PnlSnapshotRow, LifecycleEventRow, _utcnow,
)
from .decisions import (
    APPROVED, STAGE_PURCHASED, STAGE_LISTED, STAGE_SOLD, STAGE_CLOSED,
    SALE_LISTED, SALE_SOLD, SALE_UNSOLD_HOLDING, SALE_RELISTED,
    SALE_LIQUIDATED, SALE_RETURNED, SALE_WRITTEN_OFF, SALE_ABANDONED,
    FINALIZED_SALE_STATUSES, INVENTORY_STATUSES,
    EVENT_PURCHASED, EVENT_LISTED, EVENT_RELISTED, EVENT_SOLD,
    EVENT_LIQUIDATED, EVENT_RETURNED, EVENT_WRITTEN_OFF,
    EVENT_ABANDONED, EVENT_UNSOLD_HOLDING, EVENT_UPDATED,
)
from .pnl import recompute_pnl

log = logging.getLogger("trades")


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes — normalize to UTC-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_tz.utc)
    return dt


# ── Lifecycle event logging ─────────────────────────────────────────

def _log_event(
    session,
    candidate_id: int,
    event_type: str,
    *,
    purchase_id: Optional[int] = None,
    sale_id: Optional[int] = None,
    previous_stage: Optional[str] = None,
    new_stage: Optional[str] = None,
    payload: Optional[dict] = None,
    notes: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> None:
    session.add(LifecycleEventRow(
        candidate_id=candidate_id,
        purchase_id=purchase_id,
        sale_id=sale_id,
        event_type=event_type,
        previous_stage=previous_stage,
        new_stage=new_stage,
        payload=payload or {},
        notes=notes,
        occurred_at=occurred_at or _utcnow(),
    ))


# ═══════════════════════════════════════════════════════════════════
# Purchases
# ═══════════════════════════════════════════════════════════════════

def record_purchase(
    candidate_id: int,
    *,
    actual_purchase_price: float,
    purchased_at: Optional[datetime] = None,
    tax_paid: float = 0.0,
    inbound_shipping_cost: float = 0.0,
    repair_cost: float = 0.0,
    misc_buy_costs: float = 0.0,
    marketplace_purchased_from: Optional[str] = None,
    purchase_url: Optional[str] = None,
    purchase_notes: Optional[str] = None,
    seller_risk_notes: Optional[str] = None,
) -> int:
    """
    Record that an approved candidate was purchased.

    `purchased_at` may be passed manually if you're recording a past purchase.
    Snapshots predicted values for predicted-vs-actual analytics.
    """
    purchased_at = purchased_at or _utcnow()

    with session_scope() as s:
        cand = s.query(ReviewCandidateRow).filter_by(id=candidate_id).first()
        if not cand:
            raise ValueError(f"Candidate {candidate_id} not found")
        if cand.decision != APPROVED:
            raise ValueError(
                f"Candidate {candidate_id} has decision '{cand.decision}' — "
                f"must be 'approved' to record a purchase"
            )

        existing = s.query(PurchaseRecordRow).filter_by(
            candidate_id=candidate_id).first()
        if existing:
            raise ValueError(
                f"Candidate {candidate_id} already has purchase record #{existing.id}"
            )

        row = PurchaseRecordRow(
            candidate_id=candidate_id,
            purchased_at=purchased_at,
            actual_purchase_price=actual_purchase_price,
            tax_paid=tax_paid,
            inbound_shipping_cost=inbound_shipping_cost,
            repair_cost=repair_cost,
            misc_buy_costs=misc_buy_costs,
            marketplace_purchased_from=marketplace_purchased_from,
            purchase_url=purchase_url,
            purchase_notes=purchase_notes,
            seller_risk_notes=seller_risk_notes,
            predicted_resale=cand.expected_resale,
            predicted_profit=cand.net_profit,
            predicted_roi=cand.roi,
            predicted_confidence=cand.confidence,
        )
        s.add(row)
        s.flush()
        purchase_id = row.id

        previous_stage = cand.lifecycle_stage
        cand.lifecycle_stage = STAGE_PURCHASED

        _log_event(
            s, candidate_id, EVENT_PURCHASED,
            purchase_id=purchase_id,
            previous_stage=previous_stage, new_stage=STAGE_PURCHASED,
            payload={
                "actual_purchase_price": actual_purchase_price,
                "marketplace": marketplace_purchased_from,
            },
            occurred_at=purchased_at,
            notes=purchase_notes,
        )

    recompute_pnl(purchase_id)
    log.info("purchase recorded: candidate=%d purchase=%d price=%.2f",
             candidate_id, purchase_id, actual_purchase_price)
    return purchase_id


def update_purchase(purchase_id: int, **fields) -> None:
    """Update editable fields on a purchase record."""
    allowed = {
        "actual_purchase_price", "tax_paid", "inbound_shipping_cost",
        "repair_cost", "misc_buy_costs", "marketplace_purchased_from",
        "purchase_url", "purchase_notes", "seller_risk_notes",
        "purchased_at",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    with session_scope() as s:
        row = s.query(PurchaseRecordRow).filter_by(id=purchase_id).first()
        if not row:
            raise ValueError(f"Purchase {purchase_id} not found")
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_at = _utcnow()

        _log_event(
            s, row.candidate_id, EVENT_UPDATED,
            purchase_id=purchase_id,
            payload={"fields_updated": list(fields.keys())},
        )
    recompute_pnl(purchase_id)


# ═══════════════════════════════════════════════════════════════════
# Sales
# ═══════════════════════════════════════════════════════════════════

def record_sale_listing(
    purchase_id: int,
    *,
    sale_platform: Optional[str] = None,
    listed_at: Optional[datetime] = None,
    relist: bool = False,
) -> int:
    """
    Mark a purchase as listed for resale.
    Set relist=True when re-listing after a previous attempt.
    """
    listed_at = listed_at or _utcnow()
    event_type = EVENT_RELISTED if relist else EVENT_LISTED
    new_status = SALE_RELISTED if relist else SALE_LISTED

    with session_scope() as s:
        purchase = s.query(PurchaseRecordRow).filter_by(id=purchase_id).first()
        if not purchase:
            raise ValueError(f"Purchase {purchase_id} not found")

        sale = s.query(SaleRecordRow).filter_by(purchase_id=purchase_id).first()
        if not sale:
            sale = SaleRecordRow(
                purchase_id=purchase_id,
                sale_status=new_status,
                listed_at=listed_at,
                sale_platform=sale_platform,
            )
            s.add(sale)
        else:
            sale.sale_status = new_status
            sale.listed_at = listed_at
            sale.sale_platform = sale_platform or sale.sale_platform
            sale.updated_at = _utcnow()

        s.flush()
        sale_id = sale.id

        cand = s.query(ReviewCandidateRow).filter_by(
            id=purchase.candidate_id).first()
        previous_stage = cand.lifecycle_stage if cand else None
        if cand:
            cand.lifecycle_stage = STAGE_LISTED

        _log_event(
            s, purchase.candidate_id, event_type,
            purchase_id=purchase_id, sale_id=sale_id,
            previous_stage=previous_stage, new_stage=STAGE_LISTED,
            payload={"sale_platform": sale_platform},
            occurred_at=listed_at,
        )

    recompute_pnl(purchase_id)
    return sale_id


def record_sale_completed(
    purchase_id: int,
    *,
    actual_sale_price: float,
    sale_date: Optional[datetime] = None,
    listed_at: Optional[datetime] = None,
    sale_platform: Optional[str] = None,
    outbound_shipping_cost: float = 0.0,
    selling_fees: float = 0.0,
    payment_processing_fees: float = 0.0,
    return_costs: float = 0.0,
    final_notes: Optional[str] = None,
    sold_via: str = SALE_SOLD,        # SALE_SOLD or SALE_LIQUIDATED
) -> int:
    """
    Record a completed sale. Finalizes P&L.
    Use sold_via=SALE_LIQUIDATED for fire sales below estimate.
    Both `sale_date` and `listed_at` may be passed manually for backfill.
    """
    if sold_via not in (SALE_SOLD, SALE_LIQUIDATED):
        raise ValueError(f"sold_via must be 'sold' or 'liquidated', got {sold_via}")

    sale_date = sale_date or _utcnow()
    event_type = EVENT_LIQUIDATED if sold_via == SALE_LIQUIDATED else EVENT_SOLD

    with session_scope() as s:
        purchase = s.query(PurchaseRecordRow).filter_by(id=purchase_id).first()
        if not purchase:
            raise ValueError(f"Purchase {purchase_id} not found")

        sale = s.query(SaleRecordRow).filter_by(purchase_id=purchase_id).first()
        if not sale:
            sale = SaleRecordRow(
                purchase_id=purchase_id,
                listed_at=listed_at or purchase.purchased_at,
            )
            s.add(sale)
        elif listed_at:
            # Caller wants to override listed_at
            sale.listed_at = listed_at

        sale.sale_status = sold_via
        sale.sale_date = sale_date
        sale.sale_platform = sale_platform or sale.sale_platform
        sale.actual_sale_price = actual_sale_price
        sale.outbound_shipping_cost = outbound_shipping_cost
        sale.selling_fees = selling_fees
        sale.payment_processing_fees = payment_processing_fees
        sale.return_costs = return_costs
        sale.final_notes = final_notes
        sale.updated_at = _utcnow()

        # Compute days_to_sell (timezone-safe)
        if sale.listed_at and sale_date:
            listed = _ensure_utc(sale.listed_at)
            sd = _ensure_utc(sale_date)
            sale.days_to_sell = max(0, (sd - listed).days)

        s.flush()
        sale_id = sale.id

        cand = s.query(ReviewCandidateRow).filter_by(
            id=purchase.candidate_id).first()
        previous_stage = cand.lifecycle_stage if cand else None
        if cand:
            cand.lifecycle_stage = STAGE_SOLD

        _log_event(
            s, purchase.candidate_id, event_type,
            purchase_id=purchase_id, sale_id=sale_id,
            previous_stage=previous_stage, new_stage=STAGE_SOLD,
            payload={
                "actual_sale_price": actual_sale_price,
                "sale_platform": sale_platform,
                "days_to_sell": sale.days_to_sell,
            },
            notes=final_notes,
            occurred_at=sale_date,
        )

    recompute_pnl(purchase_id)
    log.info("sale recorded: purchase=%d via=%s price=%.2f",
             purchase_id, sold_via, actual_sale_price)
    return sale_id


def record_sale_unsold_holding(
    purchase_id: int,
    *,
    notes: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> int:
    """
    Mark as listed-but-unsold while still holding inventory.
    NOT a write-off. P&L stays unrealized.
    """
    occurred_at = occurred_at or _utcnow()
    with session_scope() as s:
        purchase = s.query(PurchaseRecordRow).filter_by(id=purchase_id).first()
        if not purchase:
            raise ValueError(f"Purchase {purchase_id} not found")

        sale = s.query(SaleRecordRow).filter_by(purchase_id=purchase_id).first()
        if not sale:
            sale = SaleRecordRow(purchase_id=purchase_id)
            s.add(sale)

        sale.sale_status = SALE_UNSOLD_HOLDING
        sale.final_notes = notes
        sale.updated_at = _utcnow()
        s.flush()
        sale_id = sale.id

        cand = s.query(ReviewCandidateRow).filter_by(
            id=purchase.candidate_id).first()
        previous_stage = cand.lifecycle_stage if cand else None
        # Stay in 'listed' lifecycle since we're still holding/trying to sell
        if cand:
            cand.lifecycle_stage = STAGE_LISTED

        _log_event(
            s, purchase.candidate_id, EVENT_UNSOLD_HOLDING,
            purchase_id=purchase_id, sale_id=sale_id,
            previous_stage=previous_stage, new_stage=STAGE_LISTED,
            notes=notes,
            occurred_at=occurred_at,
        )

    recompute_pnl(purchase_id)
    return sale_id


def record_sale_closed(
    purchase_id: int,
    *,
    final_status: str,        # SALE_RETURNED | SALE_WRITTEN_OFF | SALE_ABANDONED
    final_notes: Optional[str] = None,
    return_costs: float = 0.0,
    occurred_at: Optional[datetime] = None,
) -> int:
    """
    Close a trade as returned/written-off/abandoned.
    These are P&L-finalizing statuses (counted as losses).
    """
    if final_status not in (SALE_RETURNED, SALE_WRITTEN_OFF, SALE_ABANDONED):
        raise ValueError(
            f"final_status must be one of: returned, written_off, abandoned. "
            f"Got: {final_status}"
        )

    occurred_at = occurred_at or _utcnow()
    event_type_map = {
        SALE_RETURNED: EVENT_RETURNED,
        SALE_WRITTEN_OFF: EVENT_WRITTEN_OFF,
        SALE_ABANDONED: EVENT_ABANDONED,
    }
    event_type = event_type_map[final_status]

    with session_scope() as s:
        purchase = s.query(PurchaseRecordRow).filter_by(id=purchase_id).first()
        if not purchase:
            raise ValueError(f"Purchase {purchase_id} not found")

        sale = s.query(SaleRecordRow).filter_by(purchase_id=purchase_id).first()
        if not sale:
            sale = SaleRecordRow(purchase_id=purchase_id)
            s.add(sale)

        sale.sale_status = final_status
        sale.return_costs = return_costs
        sale.final_notes = final_notes
        sale.updated_at = _utcnow()
        s.flush()
        sale_id = sale.id

        cand = s.query(ReviewCandidateRow).filter_by(
            id=purchase.candidate_id).first()
        previous_stage = cand.lifecycle_stage if cand else None
        if cand:
            cand.lifecycle_stage = STAGE_CLOSED

        _log_event(
            s, purchase.candidate_id, event_type,
            purchase_id=purchase_id, sale_id=sale_id,
            previous_stage=previous_stage, new_stage=STAGE_CLOSED,
            payload={"return_costs": return_costs},
            notes=final_notes,
            occurred_at=occurred_at,
        )

    recompute_pnl(purchase_id)
    return sale_id


def get_lifecycle_history(candidate_id: int) -> list[dict]:
    """Return all lifecycle events for a candidate, oldest first."""
    with session_scope() as s:
        events = (s.query(LifecycleEventRow)
                  .filter_by(candidate_id=candidate_id)
                  .order_by(LifecycleEventRow.occurred_at.asc())
                  .all())
        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "previous_stage": e.previous_stage,
                "new_stage": e.new_stage,
                "payload": e.payload,
                "notes": e.notes,
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            }
            for e in events
        ]
