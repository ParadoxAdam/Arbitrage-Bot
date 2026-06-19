"""
Regression tests for v15.5.2 — version consistency, carrier-aware anchors,
storage-aware own outcomes, and config comment correctness.
"""
import inspect
import pytest
from app.config import (
    APP_VERSION, VALUATION_VERSION, CURRENT_ENGINE_VERSION, settings,
)
from app.valuation import value_listing, find_anchor, find_anchor_loose
from app.valuation import engine as valuation_engine
from app.models import Listing, NormalizedIdentity, CompMatch, _utcnow


def _listing(title, price=400, condition="good"):
    return Listing(
        id="t", source="ebay", source_item_id="t", source_url="",
        title=title, brand="Apple", category="phones",
        price=price, shipping=0, condition=condition,
        scraped_at=_utcnow(), raw={},
    )


def _identity(model="iphone 14 pro", storage_gb=128, condition="good",
              carrier="unlocked"):
    return NormalizedIdentity(
        brand="Apple", model=model, category="phones",
        storage_gb=storage_gb, condition=condition, carrier=carrier,
    )


def _comp_match(expected_resale=440, sample_size=8, source="active",
                confidence=0.55):
    titles = [f"iPhone 14 Pro 128GB Unlocked Good Condition #{i}"
              for i in range(5)]
    prices = [420, 430, 440, 445, 450]
    return CompMatch(
        fair_value=expected_resale * 0.74,
        expected_resale=expected_resale,
        confidence=confidence, sample_size=sample_size,
        liquidity=0.5, source=source, match_quality=0.85,
        match_details="exact",
        comp_evidence=[{"price": p, "title": t}
                       for p, t in zip(prices, titles)],
    )


# ── 1. Version consistency ─────────────────────────────────────────

class TestVersionConsistency:
    def test_versions_are_v15_5_2(self):
        """Sanity check — versions start with v15.5 (any patch level)."""
        assert APP_VERSION.startswith("v15.5")
        assert VALUATION_VERSION.startswith("v15.5")

    def test_engine_uses_central_version(self):
        """Engine must import VALUATION_VERSION from app.config, not redefine it."""
        # The engine module should expose the same constant as config
        assert valuation_engine.VALUATION_VERSION == VALUATION_VERSION

    def test_engine_module_does_not_hardcode_version(self):
        """Source code should not contain a hardcoded VALUATION_VERSION = ..."""
        src = inspect.getsource(valuation_engine)
        # The module imports from config, but doesn't redefine the constant
        # via assignment. Check for the assignment pattern.
        assignment = "VALUATION_VERSION = "
        # Allowed: nothing. The constant should only be IMPORTED.
        offending_lines = [
            line for line in src.splitlines()
            if assignment in line and not line.strip().startswith("#")
        ]
        assert len(offending_lines) == 0, (
            f"engine.py still hardcodes VALUATION_VERSION:\n"
            + "\n".join(offending_lines)
        )

    def test_valuation_objects_use_current_version(self):
        """Valuations produced by the engine carry the central version."""
        l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
        i = _identity()
        cm = _comp_match()
        v = value_listing(l, i, cm)
        assert v.valuation_version == VALUATION_VERSION


# ── 2. Carrier-aware anchor handling ───────────────────────────────

class TestCarrierAwareAnchors:
    def test_unlocked_returns_unlocked_anchor(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro", 128,
                        carrier="unlocked")
        assert a is not None
        assert a.carrier == "unlocked"

    def test_empty_carrier_returns_none(self):
        """v15.5.2: empty carrier no longer silently treated as unlocked."""
        a = find_anchor("phones", "Apple", "iphone 14 pro", 128, carrier="")
        assert a is None

    def test_none_carrier_returns_none(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro", 128, carrier=None)
        assert a is None

    def test_locked_carrier_returns_none(self):
        """No locked-carrier anchor exists in the table; returns None."""
        a = find_anchor("phones", "Apple", "iphone 14 pro", 128, carrier="at&t")
        assert a is None

    def test_loose_lookup_returns_unlocked_anchor(self):
        """find_anchor_loose returns the unlocked anchor for callers that
        explicitly want the weak-stabiliser path."""
        a = find_anchor_loose("phones", "Apple", "iphone 14 pro", 128)
        assert a is not None
        assert a.carrier == "unlocked"

    def test_unknown_carrier_target_gets_warning_and_lower_confidence(self):
        """An iPhone listing with no carrier info uses the unlocked anchor
        loosely and gets a `carrier_unknown_anchor_weak` warning + capped conf."""
        l = _listing("Apple iPhone 14 Pro 128GB")
        i = _identity(carrier="")    # no carrier info
        cm = _comp_match(expected_resale=440)
        v = value_listing(l, i, cm)
        assert "carrier_unknown_anchor_weak" in v.warnings
        # Confidence capped because we can't trust the unlocked anchor here
        assert v.valuation_confidence <= 0.45 + 0.001
        # Anchor still surfaced for diagnostic visibility
        assert v.reference_anchor_low is not None

    def test_explicit_unlocked_target_no_warning(self):
        """Explicit 'unlocked' carrier should NOT trigger the warning."""
        l = _listing("Apple iPhone 14 Pro 128GB Unlocked")
        i = _identity(carrier="unlocked")
        cm = _comp_match(expected_resale=440)
        v = value_listing(l, i, cm)
        assert "carrier_unknown_anchor_weak" not in v.warnings


# ── 3. Storage-aware own outcomes ──────────────────────────────────

class TestStorageAwareOwnOutcomes:
    """The own_outcomes lookup must use comp_key (brand|model|storage|...)
    so 128GB and 256GB iPhones are not mixed in the same average."""

    def _make_db_with_two_storages(self, tmp_path, monkeypatch):
        """Create a fresh DB with sale records for both 128GB and 256GB
        iPhone 14 Pros."""
        db_path = tmp_path / "test.db"
        monkeypatch.setattr("app.config.settings.database_url",
                            f"sqlite:///{db_path}")
        from app import db
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.models import (
            Base, ListingRow, NormalizedListingRow, ReviewCandidateRow,
            PurchaseRecordRow, SaleRecordRow,
        )
        from datetime import datetime, timezone
        db.engine = create_engine(f"sqlite:///{db_path}", future=True)
        db.SessionLocal = sessionmaker(bind=db.engine, autoflush=False,
                                        autocommit=False, future=True)
        Base.metadata.create_all(db.engine)

        with db.session_scope() as s:
            # Insert listings for both storage tiers
            for i, (storage, sale_price) in enumerate(
                [(128, 420.0), (128, 430.0), (128, 440.0),
                 (256, 580.0), (256, 600.0)]
            ):
                lst = ListingRow(
                    source="ebay", source_item_id=f"x{i}", source_url="",
                    title=f"iPhone 14 Pro {storage}GB Unlocked",
                    category="phones", price=300, shipping=0,
                    scraped_at=_utcnow(), dedupe_key=f"k{i}",
                )
                s.add(lst)
                s.flush()
                norm = NormalizedListingRow(
                    listing_id=lst.id, brand="Apple",
                    model_name="iphone 14 pro", category="phones",
                    condition="good", spec={"storage_gb": storage},
                    comp_key=f"phones|apple|iphone 14 pro|{storage}",
                    valuation_identity_key=f"phones|apple|iphone 14 pro|{storage}|unlocked",
                )
                s.add(norm)
                cand = ReviewCandidateRow(
                    listing_id=lst.id, title=lst.title,
                    source="ebay", source_url="x",
                    brand="Apple", model_name="iphone 14 pro",
                    category="phones", condition="good", price=300,
                    shipping=0, fair_value=400, expected_resale=440,
                    net_profit=80, roi=0.27, confidence=0.5,
                    liquidity=0.5, score=0.5, risk_flags=[],
                    comp_source="active", comp_count=5,
                    match_quality=0.85, match_details="",
                    comp_evidence=[], why_passed="",
                    penalties_applied=[], status="approved",
                    decision="approved", lifecycle_stage="sold",
                    is_mock=False, dedupe_key=f"d{i}",
                    engine_version="v15.5.2",
                )
                s.add(cand)
                s.flush()
                purchase = PurchaseRecordRow(
                    candidate_id=cand.id,
                    purchased_at=_utcnow(),
                    actual_purchase_price=300,
                    predicted_resale=440, predicted_profit=80,
                    predicted_roi=0.27, predicted_confidence=0.5,
                )
                s.add(purchase)
                s.flush()
                sale = SaleRecordRow(
                    purchase_id=purchase.id, sale_status="sold",
                    actual_sale_price=sale_price,
                )
                s.add(sale)

    def test_lookup_returns_only_matching_storage(self, tmp_path, monkeypatch):
        """Looking up own_outcomes for a 128GB candidate must return only
        the 128GB sale prices, not the 256GB ones."""
        self._make_db_with_two_storages(tmp_path, monkeypatch)

        from app.pipeline import _lookup_own_outcomes
        i_128 = NormalizedIdentity(
            brand="Apple", model="iphone 14 pro", category="phones",
            storage_gb=128, carrier="unlocked", condition="good",
        )
        outcomes = _lookup_own_outcomes(i_128)
        # Only the 128GB sales (£420, £430, £440)
        assert sorted(outcomes) == [420.0, 430.0, 440.0]
        assert 580.0 not in outcomes
        assert 600.0 not in outcomes

    def test_lookup_returns_only_256gb_when_targeting_256(self, tmp_path, monkeypatch):
        self._make_db_with_two_storages(tmp_path, monkeypatch)

        from app.pipeline import _lookup_own_outcomes
        i_256 = NormalizedIdentity(
            brand="Apple", model="iphone 14 pro", category="phones",
            storage_gb=256, carrier="unlocked", condition="good",
        )
        outcomes = _lookup_own_outcomes(i_256)
        assert sorted(outcomes) == [580.0, 600.0]
        assert 420.0 not in outcomes


# ── 4. Config comment / default consistency ────────────────────────

class TestConfigDefaults:
    def test_use_v2_for_profit_default_is_true(self):
        """The default must match what the comment says."""
        assert settings.use_v2_for_profit is True

    def test_use_v2_for_profit_comment_matches_default(self):
        """Inspect the source: the comment about the default must say True."""
        import app.config as cfg
        src = inspect.getsource(cfg)
        # Find the use_v2_for_profit block
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "use_v2_for_profit" in line and "bool" in line:
                # Check the preceding comment block (up to 5 lines back)
                comment_block = " ".join(lines[max(0, i-6):i])
                # The comment should mention "True (default)" not "False (default)"
                assert "False (default)" not in comment_block, (
                    "config comment claims default is False but it's actually True"
                )
                # And should affirmatively describe True as default
                assert "True" in comment_block
                return
        raise AssertionError("use_v2_for_profit setting not found in config source")
