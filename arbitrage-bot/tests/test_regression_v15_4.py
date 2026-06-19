"""
Regression tests for v15.4 — engine versioning, recheck policy,
and structured failure-reason persistence.
"""
import pytest
from datetime import datetime, timezone, timedelta
from app.config import CURRENT_ENGINE_VERSION
from app.db import session_scope
from app.models import (
    Base, ListingRow, OpportunityRow, ReviewCandidateRow,
    ScanRunRow, _utcnow,
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


# ── Engine version stamping ─────────────────────────────────────────

def test_current_engine_version_is_v15_4():
    """The constant exists. Bumped per release; just sanity check format."""
    assert CURRENT_ENGINE_VERSION.startswith("v15.")


def test_engine_version_filter_accepts_current_keyword():
    from app.analytics import _resolve_engine_filter
    assert _resolve_engine_filter("current") == CURRENT_ENGINE_VERSION


def test_engine_version_filter_handles_none_and_all():
    from app.analytics import _resolve_engine_filter
    assert _resolve_engine_filter(None) is None
    assert _resolve_engine_filter("all") is None
    assert _resolve_engine_filter("") is None


def test_engine_version_filter_passes_through_explicit_version():
    from app.analytics import _resolve_engine_filter
    assert _resolve_engine_filter("v15.0") == "v15.0"


# ── Failure reason codes ────────────────────────────────────────────

def test_all_failure_reasons_have_qstats_mapping():
    """Every failure reason code must map to a counter field."""
    from app.decisions import (
        ALL_FAILURE_REASONS, FAILURE_REASON_TO_QSTATS_FIELD,
    )
    for reason in ALL_FAILURE_REASONS:
        assert reason in FAILURE_REASON_TO_QSTATS_FIELD


def test_failure_reason_codes_for_low_profit():
    """A low-profit listing produces FAIL_PROFIT in its codes."""
    from types import SimpleNamespace
    from app.pipeline import _failure_reason_codes
    from app.decisions import FAIL_PROFIT, FAIL_ROI

    op = SimpleNamespace(
        net_profit=10.0, roi=0.05, score=0.10, confidence=0.6,
        match_quality=0.85, comp_source="active",
        risk_flags=[],
    )
    codes = _failure_reason_codes(op)
    assert FAIL_PROFIT in codes
    assert FAIL_ROI in codes


def test_failure_reason_codes_for_active_only():
    from types import SimpleNamespace
    from app.pipeline import _failure_reason_codes
    from app.decisions import FAIL_ACTIVE_ONLY
    op = SimpleNamespace(
        net_profit=100, roi=0.5, score=0.7, confidence=0.6,
        match_quality=0.85, comp_source="active",
        risk_flags=[],
    )
    codes = _failure_reason_codes(op)
    assert FAIL_ACTIVE_ONLY in codes


def test_failure_reason_codes_for_battery_health():
    from types import SimpleNamespace
    from app.pipeline import _failure_reason_codes
    from app.decisions import FAIL_BATTERY_HEALTH
    op = SimpleNamespace(
        net_profit=100, roi=0.5, score=0.7, confidence=0.6,
        match_quality=0.85, comp_source="sold",
        risk_flags=["missing_battery_health"],
    )
    codes = _failure_reason_codes(op)
    assert FAIL_BATTERY_HEALTH in codes


# ── Recheck policy ──────────────────────────────────────────────────

def test_should_rescore_existing_for_price_change():
    from app.pipeline import _should_rescore_existing

    with session_scope() as s:
        s.add(ListingRow(
            source="ebay", source_item_id="x", source_url="u", title="t",
            category="phones", price=100, shipping=0,
            scraped_at=_utcnow(), dedupe_key="dk1",
            last_scored_at=_utcnow(), last_seen_price=100.0,
            is_near_miss=False,
        ))
        s.flush()

    with session_scope() as s:
        existing = s.query(ListingRow).filter_by(dedupe_key="dk1").first()
        should, reason = _should_rescore_existing(
            existing, 110.0, _utcnow(),
        )
    assert should is True
    assert "price" in reason.lower()


def test_should_rescore_existing_for_staleness():
    from app.pipeline import _should_rescore_existing

    yesterday = _utcnow() - timedelta(hours=48)
    with session_scope() as s:
        s.add(ListingRow(
            source="ebay", source_item_id="y", source_url="u", title="t",
            category="phones", price=100, shipping=0,
            scraped_at=yesterday, dedupe_key="dk2",
            last_scored_at=yesterday, last_seen_price=100.0,
            is_near_miss=False,
        ))
        s.flush()

    with session_scope() as s:
        existing = s.query(ListingRow).filter_by(dedupe_key="dk2").first()
        should, reason = _should_rescore_existing(
            existing, 100.0, _utcnow(),
        )
    assert should is True
    assert "stale" in reason.lower()


def test_should_rescore_existing_for_near_miss():
    from app.pipeline import _should_rescore_existing

    with session_scope() as s:
        s.add(ListingRow(
            source="ebay", source_item_id="z", source_url="u", title="t",
            category="phones", price=100, shipping=0,
            scraped_at=_utcnow(), dedupe_key="dk3",
            last_scored_at=_utcnow(), last_seen_price=100.0,
            is_near_miss=True,
        ))
        s.flush()

    with session_scope() as s:
        existing = s.query(ListingRow).filter_by(dedupe_key="dk3").first()
        should, reason = _should_rescore_existing(
            existing, 100.0, _utcnow(),
        )
    assert should is True
    assert reason == "near_miss"


def test_should_skip_existing_when_fresh_and_unchanged():
    from app.pipeline import _should_rescore_existing

    with session_scope() as s:
        s.add(ListingRow(
            source="ebay", source_item_id="w", source_url="u", title="t",
            category="phones", price=100, shipping=0,
            scraped_at=_utcnow(), dedupe_key="dk4",
            last_scored_at=_utcnow(), last_seen_price=100.0,
            is_near_miss=False,
        ))
        s.flush()

    with session_scope() as s:
        existing = s.query(ListingRow).filter_by(dedupe_key="dk4").first()
        should, reason = _should_rescore_existing(
            existing, 100.0, _utcnow(),
        )
    assert should is False
