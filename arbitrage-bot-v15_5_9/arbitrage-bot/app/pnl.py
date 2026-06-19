"""
P&L computation and predicted-vs-actual analytics.

Key principle: only finalized statuses count toward P&L.
  - SOLD / LIQUIDATED → realized profit (or loss)
  - RETURNED / WRITTEN_OFF / ABANDONED → realized loss
  - LISTED / UNSOLD_HOLDING / RELISTED → unrealized (still in inventory)
"""
from __future__ import annotations
import logging
from .db import session_scope
from .models import (
    PurchaseRecordRow, SaleRecordRow, PnlSnapshotRow,
    ReviewCandidateRow, _utcnow,
)
from .decisions import (
    SALE_SOLD, SALE_LIQUIDATED, SALE_RETURNED, SALE_WRITTEN_OFF,
    SALE_ABANDONED, FINALIZED_SALE_STATUSES, SALE_HAS_PROCEEDS,
)

log = logging.getLogger("pnl")


def recompute_pnl(purchase_id: int) -> None:
    """
    Recompute P&L snapshot for a purchase. Idempotent.
    Called whenever a purchase or sale record changes.
    """
    with session_scope() as s:
        purchase = s.query(PurchaseRecordRow).filter_by(id=purchase_id).first()
        if not purchase:
            return

        sale = s.query(SaleRecordRow).filter_by(purchase_id=purchase_id).first()

        # Cost basis (always known once purchased)
        cost_basis = (
            purchase.actual_purchase_price
            + purchase.tax_paid
            + purchase.inbound_shipping_cost
            + purchase.repair_cost
            + purchase.misc_buy_costs
        )

        gross_proceeds = 0.0
        total_cost = cost_basis
        net_profit = 0.0
        roi = 0.0
        is_finalized = False

        if sale:
            sale_costs = (
                sale.outbound_shipping_cost
                + sale.selling_fees
                + sale.payment_processing_fees
                + sale.return_costs
            )
            total_cost = cost_basis + sale_costs

            if sale.sale_status in SALE_HAS_PROCEEDS:
                # SOLD or LIQUIDATED — profit could be positive or negative
                gross_proceeds = sale.actual_sale_price
                net_profit = gross_proceeds - total_cost
                roi = (net_profit / cost_basis) if cost_basis > 0 else 0.0
                is_finalized = True

            elif sale.sale_status == SALE_RETURNED:
                # Buyer returned — full burden on us
                net_profit = -total_cost
                is_finalized = True

            elif sale.sale_status in (SALE_WRITTEN_OFF, SALE_ABANDONED):
                # Officially counted as a loss
                net_profit = -total_cost
                is_finalized = True

            # SALE_LISTED, SALE_UNSOLD_HOLDING, SALE_RELISTED:
            # unrealized — leave at 0, is_finalized stays False
            # Important: an unsold-but-still-holding item is NOT a loss.

        # Errors vs predictions (only meaningful when finalized)
        resale_error = gross_proceeds - purchase.predicted_resale
        profit_error = net_profit - purchase.predicted_profit
        roi_error = roi - purchase.predicted_roi

        snap = s.query(PnlSnapshotRow).filter_by(
            purchase_id=purchase_id).first()
        if not snap:
            snap = PnlSnapshotRow(purchase_id=purchase_id)
            s.add(snap)

        snap.actual_gross_proceeds = round(gross_proceeds, 2)
        snap.actual_total_cost = round(total_cost, 2)
        snap.actual_net_profit = round(net_profit, 2)
        snap.actual_roi = round(roi, 4)
        snap.predicted_resale = round(purchase.predicted_resale, 2)
        snap.predicted_profit = round(purchase.predicted_profit, 2)
        snap.predicted_roi = round(purchase.predicted_roi, 4)
        snap.resale_error = round(resale_error, 2)
        snap.profit_error = round(profit_error, 2)
        snap.roi_error = round(roi_error, 4)
        snap.is_finalized = is_finalized
        snap.updated_at = _utcnow()


def get_pnl_summary(*, exclude_mock: bool = True,
                    engine_version: str | None = "current") -> dict:
    """Aggregate P&L across all finalized snapshots."""
    from .config import CURRENT_ENGINE_VERSION
    if engine_version in (None, "all", ""):
        ev = None
    elif engine_version == "current":
        ev = CURRENT_ENGINE_VERSION
    else:
        ev = engine_version
    with session_scope() as s:
        q = (s.query(PnlSnapshotRow, PurchaseRecordRow, ReviewCandidateRow)
             .join(PurchaseRecordRow,
                   PnlSnapshotRow.purchase_id == PurchaseRecordRow.id)
             .join(ReviewCandidateRow,
                   PurchaseRecordRow.candidate_id == ReviewCandidateRow.id))

        if exclude_mock:
            q = q.filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
        if ev:
            q = q.filter(ReviewCandidateRow.engine_version == ev)

        rows = q.all()

        total_count = len(rows)
        finalized = [r for r in rows if r[0].is_finalized]
        sold = [r for r in finalized if r[0].actual_gross_proceeds > 0]

        # Items still in inventory (purchased but not finalized)
        inventory = [r for r in rows if not r[0].is_finalized]
        inventory_cost = sum(r[0].actual_total_cost for r in inventory)

        total_profit = sum(r[0].actual_net_profit for r in finalized)
        total_invested = sum(r[0].actual_total_cost for r in finalized)
        avg_roi = (sum(r[0].actual_roi for r in finalized) / len(finalized)
                   if finalized else 0.0)

        win_count = sum(1 for r in finalized if r[0].actual_net_profit > 0)
        loss_count = sum(1 for r in finalized if r[0].actual_net_profit <= 0)

        days_data = []
        for snap, _purchase, _cand in rows:
            sale = s.query(SaleRecordRow).filter_by(
                purchase_id=snap.purchase_id).first()
            if sale and sale.days_to_sell is not None:
                days_data.append(sale.days_to_sell)
        avg_days = sum(days_data) / len(days_data) if days_data else None

        return {
            "total_purchases": total_count,
            "finalized_count": len(finalized),
            "sold_count": len(sold),
            "inventory_count": len(inventory),
            "inventory_cost_at_risk": round(inventory_cost, 2),
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": (win_count / len(finalized)) if finalized else 0.0,
            "total_actual_profit": round(total_profit, 2),
            "total_invested": round(total_invested, 2),
            "avg_actual_roi": round(avg_roi, 4),
            "avg_days_to_sell": round(avg_days, 1) if avg_days is not None else None,
        }
