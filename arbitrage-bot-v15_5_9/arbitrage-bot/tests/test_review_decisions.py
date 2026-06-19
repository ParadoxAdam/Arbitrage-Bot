"""Tests for structured review decisions and event history."""
import pytest
from app.db import init_db, session_scope
from app.models import (
    Base, ReviewCandidateRow, ReviewDecisionEventRow, _utcnow,
)
from app.review import set_decision, get_decision_history
from app.decisions import (
    PENDING, APPROVED, WATCHLIST, NEEDS_MORE_INFO, PASSED_NO_ACTION,
    REJECTED_MOCK, REJECTED_BAD_MATCH, REJECTED_TOO_RISKY,
    STAGE_NONE, REJECTION_DECISIONS,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Use a fresh in-memory-ish DB per test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.config.settings.database_url",
                        f"sqlite:///{db_path}")
    # Reset the engine
    from app import db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db.engine = create_engine(f"sqlite:///{db_path}", future=True)
    db.SessionLocal = sessionmaker(bind=db.engine, autoflush=False,
                                    autocommit=False, future=True)
    Base.metadata.create_all(db.engine)
    yield


def _make_candidate(decision=PENDING, source="ebay", is_mock=False):
    with session_scope() as s:
        c = ReviewCandidateRow(
            title="Test Item", source=source, source_url="http://x",
            category="shoes", price=100, shipping=0,
            fair_value=80, expected_resale=150, net_profit=40, roi=0.4,
            confidence=0.8, liquidity=0.7, score=0.65,
            risk_flags=[], comp_source="sold", comp_count=8,
            match_quality=0.9, match_details="size match",
            comp_evidence=[], why_passed="test",
            penalties_applied=[], status="pending",
            decision=decision, lifecycle_stage=STAGE_NONE,
            is_mock=is_mock, dedupe_key=f"test-{_utcnow().timestamp()}",
        )
        s.add(c)
        s.flush()
        return c.id


def test_set_decision_creates_event():
    cid = _make_candidate()
    set_decision(cid, APPROVED, notes="looks great")
    history = get_decision_history(cid)
    assert len(history) == 1
    assert history[0]["previous_decision"] == PENDING
    assert history[0]["new_decision"] == APPROVED
    assert history[0]["notes"] == "looks great"


def test_set_decision_updates_candidate():
    cid = _make_candidate()
    set_decision(cid, REJECTED_BAD_MATCH, notes="size mismatch")
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.decision == REJECTED_BAD_MATCH
        assert c.decision_notes == "size mismatch"
        assert c.reviewed_at is not None


def test_decision_keeps_event_history():
    cid = _make_candidate()
    set_decision(cid, WATCHLIST, notes="check in 24h")
    set_decision(cid, NEEDS_MORE_INFO, notes="want photos")
    set_decision(cid, APPROVED, notes="finally good")
    history = get_decision_history(cid)
    assert len(history) == 3
    assert [h["new_decision"] for h in history] == [
        WATCHLIST, NEEDS_MORE_INFO, APPROVED,
    ]
    assert history[1]["previous_decision"] == WATCHLIST


def test_invalid_decision_raises():
    cid = _make_candidate()
    with pytest.raises(ValueError):
        set_decision(cid, "not_a_real_decision")


def test_watchlist_flag_set_when_watchlisting():
    cid = _make_candidate()
    set_decision(cid, WATCHLIST)
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.watchlist is True


def test_watchlist_flag_cleared_when_moving_off_watchlist():
    cid = _make_candidate()
    set_decision(cid, WATCHLIST)
    set_decision(cid, APPROVED)
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.watchlist is False


def test_legacy_status_synced_for_backward_compat():
    cid = _make_candidate()
    set_decision(cid, APPROVED)
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.status == "approved"

    set_decision(cid, REJECTED_TOO_RISKY)
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.status == "rejected"


def test_passed_no_action_is_separate_from_rejection():
    cid = _make_candidate()
    set_decision(cid, PASSED_NO_ACTION, notes="not the right size")
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.decision == PASSED_NO_ACTION
        assert c.decision not in REJECTION_DECISIONS


def test_mock_rejection_is_distinguishable():
    cid = _make_candidate(source="mock", is_mock=True)
    set_decision(cid, REJECTED_MOCK, notes="test data")
    with session_scope() as s:
        c = s.query(ReviewCandidateRow).filter_by(id=cid).first()
        assert c.is_mock is True
        assert c.decision == REJECTED_MOCK
