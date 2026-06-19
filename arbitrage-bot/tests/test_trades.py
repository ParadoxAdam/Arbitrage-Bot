"""Tests for purchase + sale tracking."""
import pytest
from datetime import datetime, timezone, timedelta
from app.db import session_scope
from app.models import (
    Base, ReviewCandidateRow, PurchaseRecordRow, SaleRecordRow,
    PnlSnapshotRow, _utcnow,
)
from app.review import set_decision
from app.trades import (
    record_purchase, record_sale_completed, record_sale_listing,
    record_sale_closed,
)
from app.decisions import (
    PENDING, APPROVED, STAGE_PURCHASED, STAGE_LISTED, STAGE_SOLD,
    STAGE_CLOSED, SALE_LISTED, SALE_SOLD, SALE_UNSOLD_HOLDING,
    SALE_WRITTEN_OFF, SALE_ABANDONED,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.config.settings.database_url",
                        f"sqlite:///{db_path}")
    from app import db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db.engine = create_engine(f"sqlite:///{db_path}", future=True)
    db.SessionLocal = sessionmaker(bind=db.engine, autoflush=False,
                                    autocommit=False, future=True)
    Base.metadata.create_all(db.engine)
    yield


def _make_approved_candidate(price=100, expected_resale=200, net_profit=80):
    with session_scope() as s:
        c = ReviewCandidateRow(
            title="Test", source="ebay", source_url="http://x",
            category="shoes", price=price, shipping=0,
            fair_value=expected_resale * 0.74, expected_resale=expected_resale,
            net_profit=net_profit, roi=net_profit / price,
            confidence=0.85, liquidity=0.7, score=0.7,
            risk_flags=[], comp_source="sold", comp_count=10,
            match_quality=0.9, match_details="match",
            comp_evidence=[], why_passed="test",
            penalties_applied=[], status="approved",
            decision=APPROVED, lifecycle_stage="none",
            is_mock=False, dedupe_key=f"test-{_utcnow().timestamp()}",
        )
        s.add(c)
        s.flush()
        return c.id


def test_record_purchase_basic():
    cid = _make_approved_candidate(price=100, expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=95.0)
    with session_scope() as s:
        p = s.query(PurchaseRecordRow).filter_by(id=pid).first()
        assert p.actual_purchase_price == 95.0
        # Predictions snapshotted
        assert p.predicted_resale == 200
        assert p.predicted_profit == 80
        # Lifecycle stage updated
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.lifecycle_stage == STAGE_PURCHASED


def test_purchase_requires_approved_decision():
    with session_scope() as s:
        c = ReviewCandidateRow(
            title="x", source="ebay", source_url="http://x",
            category="shoes", price=100, shipping=0,
            fair_value=80, expected_resale=150, net_profit=40, roi=0.4,
            confidence=0.8, liquidity=0.7, score=0.65,
            risk_flags=[], comp_source="sold", comp_count=8,
            match_quality=0.9, match_details="m", comp_evidence=[],
            why_passed="t", penalties_applied=[], status="pending",
            decision=PENDING, lifecycle_stage="none",
            is_mock=False, dedupe_key="test",
        )
        s.add(c)
        s.flush()
        cid = c.id

    with pytest.raises(ValueError, match="must be 'approved'"):
        record_purchase(cid, actual_purchase_price=95.0)


def test_purchase_idempotent_blocks_double_recording():
    cid = _make_approved_candidate()
    record_purchase(cid, actual_purchase_price=100)
    with pytest.raises(ValueError, match="already has purchase"):
        record_purchase(cid, actual_purchase_price=100)


def test_pnl_snapshot_created_on_purchase():
    cid = _make_approved_candidate(price=100, expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=95)
    with session_scope() as s:
        snap = s.query(PnlSnapshotRow).filter_by(purchase_id=pid).first()
        assert snap is not None
        assert snap.is_finalized is False  # nothing sold yet


def test_record_sale_completed_finalizes_pnl():
    cid = _make_approved_candidate(price=100, expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=95, inbound_shipping_cost=5)
    record_sale_completed(
        pid, actual_sale_price=210,
        outbound_shipping_cost=8, selling_fees=27, payment_processing_fees=6,
    )
    with session_scope() as s:
        snap = s.query(PnlSnapshotRow).filter_by(purchase_id=pid).first()
        assert snap.is_finalized is True
        # Cost = 95 + 5 + 8 + 27 + 6 = 141
        # Profit = 210 - 141 = 69
        assert snap.actual_total_cost == 141.0
        assert snap.actual_net_profit == 69.0
        # Predicted profit was 80, actual 69 -> error -11
        assert snap.profit_error == -11.0

        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.lifecycle_stage == STAGE_SOLD


def test_listing_stage():
    cid = _make_approved_candidate()
    pid = record_purchase(cid, actual_purchase_price=100)
    record_sale_listing(pid, sale_platform="ebay")
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.lifecycle_stage == STAGE_LISTED
        sale = s.query(SaleRecordRow).filter_by(purchase_id=pid).first()
        assert sale.sale_status == SALE_LISTED


def test_record_sale_closed_written_off():
    cid = _make_approved_candidate(price=100, expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=95)
    record_sale_closed(pid, final_status=SALE_WRITTEN_OFF,
                       final_notes="couldn't sell, writing off")
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.lifecycle_stage == STAGE_CLOSED
        snap = s.query(PnlSnapshotRow).filter_by(purchase_id=pid).first()
        assert snap.is_finalized is True
        assert snap.actual_net_profit < 0  # full cost as loss


def test_unsold_holding_is_not_a_loss():
    """Unsold-still-holding is unrealized — should NOT be a loss."""
    from app.trades import record_sale_unsold_holding
    cid = _make_approved_candidate(price=100, expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=95)
    record_sale_unsold_holding(pid, notes="listed but no offers yet")
    with session_scope() as s:
        snap = s.query(PnlSnapshotRow).filter_by(purchase_id=pid).first()
        # Crucially: P&L is NOT finalized
        assert snap.is_finalized is False
        # And the actual profit stays at 0 (unrealized), NOT -cost
        assert snap.actual_net_profit == 0.0


def test_liquidation_records_realized_profit():
    """Liquidation = below-estimate sale, but still finalizes P&L."""
    cid = _make_approved_candidate(price=100, expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=95)
    from app.decisions import SALE_LIQUIDATED
    record_sale_completed(
        pid, actual_sale_price=120,  # below estimate but still recovered
        sold_via=SALE_LIQUIDATED,
        selling_fees=15, payment_processing_fees=5,
    )
    with session_scope() as s:
        snap = s.query(PnlSnapshotRow).filter_by(purchase_id=pid).first()
        assert snap.is_finalized is True
        # Cost = 95 + 15 + 5 = 115; profit = 120 - 115 = 5
        assert snap.actual_net_profit == 5.0
        sale = s.query(SaleRecordRow).filter_by(purchase_id=pid).first()
        assert sale.sale_status == SALE_LIQUIDATED


def test_relisting_uses_relisted_status():
    from app.trades import record_sale_listing
    from app.decisions import SALE_RELISTED
    cid = _make_approved_candidate()
    pid = record_purchase(cid, actual_purchase_price=100)
    record_sale_listing(pid, sale_platform="ebay")
    record_sale_listing(pid, sale_platform="ebay", relist=True)
    with session_scope() as s:
        sale = s.query(SaleRecordRow).filter_by(purchase_id=pid).first()
        assert sale.sale_status == SALE_RELISTED


def test_lifecycle_event_history_captures_transitions():
    from app.trades import get_lifecycle_history
    cid = _make_approved_candidate()
    pid = record_purchase(cid, actual_purchase_price=100)
    record_sale_completed(pid, actual_sale_price=200,
                          selling_fees=20, payment_processing_fees=5)
    history = get_lifecycle_history(cid)
    event_types = [e["event_type"] for e in history]
    assert "purchased" in event_types
    assert "sold" in event_types


def test_days_to_sell_computed():
    cid = _make_approved_candidate()
    pid = record_purchase(cid, actual_purchase_price=100)

    listed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sale_date = datetime(2026, 1, 10, tzinfo=timezone.utc)

    record_sale_listing(pid, sale_platform="ebay", listed_at=listed_at)
    record_sale_completed(pid, actual_sale_price=200, sale_date=sale_date)

    with session_scope() as s:
        sale = s.query(SaleRecordRow).filter_by(purchase_id=pid).first()
        assert sale.days_to_sell == 9
