"""Tests for P&L computation and analytics."""
import pytest
from app.db import session_scope
from app.models import Base, ReviewCandidateRow, _utcnow
from app.review import set_decision
from app.trades import record_purchase, record_sale_completed
from app.pnl import get_pnl_summary
from app.analytics import (
    candidate_summary, rejection_patterns, category_performance,
    predicted_vs_actual,
)
from app.decisions import (
    APPROVED, REJECTED_MOCK, REJECTED_BAD_MATCH, REJECTED_TOO_RISKY,
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


def _make_cand(category="shoes", source="ebay", is_mock=False,
               expected_resale=200, net_profit=80, decision="approved"):
    from app.config import CURRENT_ENGINE_VERSION
    with session_scope() as s:
        c = ReviewCandidateRow(
            title=f"{category} test {_utcnow().timestamp()}",
            source=source, source_url="http://x",
            category=category, price=100, shipping=0,
            fair_value=expected_resale * 0.74,
            expected_resale=expected_resale,
            net_profit=net_profit, roi=net_profit / 100,
            confidence=0.85, liquidity=0.7, score=0.7,
            risk_flags=[], comp_source="sold", comp_count=10,
            match_quality=0.9, match_details="match",
            comp_evidence=[], why_passed="test",
            penalties_applied=[], status=decision,
            decision=decision, lifecycle_stage="none",
            is_mock=is_mock, dedupe_key=f"test-{_utcnow().timestamp()}",
            engine_version=CURRENT_ENGINE_VERSION,
        )
        s.add(c)
        s.flush()
        return c.id


def test_pnl_summary_excludes_mock_by_default():
    cid_real = _make_cand(is_mock=False)
    cid_mock = _make_cand(is_mock=True)

    pid_real = record_purchase(cid_real, actual_purchase_price=100)
    pid_mock = record_purchase(cid_mock, actual_purchase_price=100)

    record_sale_completed(pid_real, actual_sale_price=200,
                          selling_fees=20, payment_processing_fees=5)
    record_sale_completed(pid_mock, actual_sale_price=200,
                          selling_fees=20, payment_processing_fees=5)

    summary = get_pnl_summary(exclude_mock=True)
    assert summary["total_purchases"] == 1   # mock excluded
    assert summary["sold_count"] == 1


def test_pnl_summary_includes_mock_when_requested():
    cid_real = _make_cand(is_mock=False)
    cid_mock = _make_cand(is_mock=True)
    pid_real = record_purchase(cid_real, actual_purchase_price=100)
    pid_mock = record_purchase(cid_mock, actual_purchase_price=100)
    record_sale_completed(pid_real, actual_sale_price=200)
    record_sale_completed(pid_mock, actual_sale_price=200)

    summary = get_pnl_summary(exclude_mock=False)
    assert summary["total_purchases"] == 2


def test_candidate_summary_excludes_mock():
    _make_cand(is_mock=False, decision="pending")
    _make_cand(is_mock=True, decision="pending")
    s = candidate_summary(exclude_mock=True)
    assert s["total"] == 1
    assert s["pending"] == 1


def test_rejection_patterns_excludes_mock_rejections():
    cid1 = _make_cand(is_mock=False, decision="pending")
    cid2 = _make_cand(is_mock=True, decision="pending")
    set_decision(cid1, REJECTED_BAD_MATCH)
    set_decision(cid2, REJECTED_MOCK)
    pat = rejection_patterns(exclude_mock=True)
    # Only the real rejection should appear
    assert pat["total_rejections"] == 1
    reasons = [r["reason"] for r in pat["by_reason"]]
    assert REJECTED_BAD_MATCH in reasons
    assert REJECTED_MOCK not in reasons


def test_category_performance_aggregates():
    cid1 = _make_cand(category="shoes", expected_resale=200, net_profit=80)
    cid2 = _make_cand(category="shoes", expected_resale=300, net_profit=100)
    cid3 = _make_cand(category="phones", expected_resale=500, net_profit=150)

    pid1 = record_purchase(cid1, actual_purchase_price=100)
    pid2 = record_purchase(cid2, actual_purchase_price=150)
    pid3 = record_purchase(cid3, actual_purchase_price=200)

    record_sale_completed(pid1, actual_sale_price=210)
    record_sale_completed(pid2, actual_sale_price=290)
    record_sale_completed(pid3, actual_sale_price=480)

    perf = category_performance()
    by_cat = {p["category"]: p for p in perf}
    assert by_cat["shoes"]["sold"] == 2
    assert by_cat["phones"]["sold"] == 1
    assert by_cat["shoes"]["avg_actual_profit"] is not None


def test_predicted_vs_actual_computes_errors():
    cid = _make_cand(expected_resale=200, net_profit=80)
    pid = record_purchase(cid, actual_purchase_price=100)
    record_sale_completed(pid, actual_sale_price=190,
                          selling_fees=20, payment_processing_fees=5)

    pva = predicted_vs_actual()
    assert pva["sample_size"] == 1
    # actual_resale was 190, predicted was 200 -> error -10
    assert pva["avg_resale_error"] == -10.0


def test_pnl_total_profit_aggregates():
    cid1 = _make_cand(expected_resale=200, net_profit=80)
    cid2 = _make_cand(expected_resale=300, net_profit=100)

    pid1 = record_purchase(cid1, actual_purchase_price=100)
    pid2 = record_purchase(cid2, actual_purchase_price=150)

    record_sale_completed(pid1, actual_sale_price=200,
                          selling_fees=15, payment_processing_fees=5)
    record_sale_completed(pid2, actual_sale_price=290,
                          selling_fees=20, payment_processing_fees=5)

    summary = get_pnl_summary()
    # Cost1 = 100 + 15 + 5 = 120  Profit1 = 200 - 120 = 80
    # Cost2 = 150 + 20 + 5 = 175  Profit2 = 290 - 175 = 115
    # Total = 195
    assert summary["total_actual_profit"] == 195.0
    assert summary["sold_count"] == 2
    assert summary["win_count"] == 2
