"""
Regression tests for v15.5.3 — separate valuation_identity_key for own_outcomes.

The v15.5.2 patch claimed comp_key was carrier-aware but it actually wasn't:
phone comp_key is "phones|apple|iphone 14 pro|256" with no carrier bucket.
v15.5.3 introduces a stricter valuation_identity_key for own_outcomes lookup
WITHOUT changing comp_key (which still drives comp matching).
"""
import pytest
from app.models import NormalizedIdentity, _utcnow


def _id(model="iphone 14 pro", storage_gb=128, carrier="unlocked"):
    return NormalizedIdentity(
        brand="Apple", model=model, category="phones",
        storage_gb=storage_gb, carrier=carrier, condition="good",
    )


# ── 1. comp_key remains unchanged ──────────────────────────────────

class TestCompKeyUnchanged:
    """v15.5.3 must NOT modify the comp_key — comp matching still works the same."""

    def test_comp_key_for_phone_does_not_include_carrier(self):
        i = _id(storage_gb=256, carrier="unlocked")
        # comp_key is brand|model|storage only — no carrier
        assert i.comp_key == "phones|apple|iphone 14 pro|256"

    def test_comp_key_same_for_unlocked_and_locked(self):
        """Comp matching shouldn't be affected by carrier — comps with
        unknown carriers should still be findable for an unlocked candidate."""
        unlocked = _id(carrier="unlocked")
        locked = _id(carrier="at&t")
        unknown = _id(carrier="")
        # All three must produce the same comp_key
        assert unlocked.comp_key == locked.comp_key == unknown.comp_key


# ── 2. valuation_identity_key separates carrier states ─────────────

class TestValuationIdentityKey:
    def test_unlocked_key_format(self):
        i = _id(storage_gb=256, carrier="unlocked")
        assert i.valuation_identity_key == \
            "phones|apple|iphone 14 pro|256|unlocked"

    def test_locked_key_format(self):
        i = _id(storage_gb=256, carrier="at&t")
        assert i.valuation_identity_key == \
            "phones|apple|iphone 14 pro|256|locked:at&t"

    def test_unknown_key_format(self):
        i = _id(storage_gb=256, carrier="")
        assert i.valuation_identity_key == \
            "phones|apple|iphone 14 pro|256|unknown"

    def test_none_carrier_treated_as_unknown(self):
        i = _id(storage_gb=256, carrier=None)
        assert i.valuation_identity_key == \
            "phones|apple|iphone 14 pro|256|unknown"

    def test_unlocked_does_not_match_locked(self):
        a = _id(carrier="unlocked")
        b = _id(carrier="at&t")
        assert a.valuation_identity_key != b.valuation_identity_key

    def test_unlocked_does_not_match_unknown(self):
        a = _id(carrier="unlocked")
        b = _id(carrier="")
        assert a.valuation_identity_key != b.valuation_identity_key

    def test_locked_does_not_match_unknown(self):
        a = _id(carrier="at&t")
        b = _id(carrier="")
        assert a.valuation_identity_key != b.valuation_identity_key

    def test_storage_still_separates(self):
        a = _id(storage_gb=128, carrier="unlocked")
        b = _id(storage_gb=256, carrier="unlocked")
        assert a.valuation_identity_key != b.valuation_identity_key

    def test_model_still_separates(self):
        a = _id(model="iphone 14 pro", carrier="unlocked")
        b = _id(model="iphone 14 pro max", carrier="unlocked")
        assert a.valuation_identity_key != b.valuation_identity_key


# ── 3. Lookup uses the new key ─────────────────────────────────────

class TestLookupUsesValuationIdentityKey:

    def _seed(self, tmp_path, monkeypatch):
        """Seed a DB with sales for unlocked, locked, and unknown-carrier
        iPhone 14 Pro 256GB. The lookup for unlocked must return only the
        unlocked sales."""
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
        db.engine = create_engine(f"sqlite:///{db_path}", future=True)
        db.SessionLocal = sessionmaker(bind=db.engine, autoflush=False,
                                        autocommit=False, future=True)
        Base.metadata.create_all(db.engine)

        seeds = [
            # (carrier, sale_price)
            ("unlocked", 580.0),
            ("unlocked", 600.0),
            ("unlocked", 620.0),
            ("at&t",     420.0),    # locked — should NOT mix
            ("at&t",     430.0),
            ("",         500.0),    # unknown — should NOT mix
            ("",         510.0),
        ]
        with db.session_scope() as s:
            for i, (carr, price) in enumerate(seeds):
                # Build the valuation_identity_key for this seed
                if not carr:
                    bucket = "unknown"
                elif "unlocked" in carr:
                    bucket = "unlocked"
                else:
                    bucket = f"locked:{carr.lower()}"
                vk = f"phones|apple|iphone 14 pro|256|{bucket}"

                lst = ListingRow(
                    source="ebay", source_item_id=f"x{i}", source_url="",
                    title=f"iPhone 14 Pro 256GB {carr or 'unknown'}",
                    category="phones", price=300, shipping=0,
                    scraped_at=_utcnow(), dedupe_key=f"k{i}",
                )
                s.add(lst)
                s.flush()
                norm = NormalizedListingRow(
                    listing_id=lst.id, brand="Apple",
                    model_name="iphone 14 pro", category="phones",
                    condition="good",
                    spec={"storage_gb": 256, "carrier": carr},
                    comp_key="phones|apple|iphone 14 pro|256",
                    valuation_identity_key=vk,
                )
                s.add(norm)
                cand = ReviewCandidateRow(
                    listing_id=lst.id, title=lst.title, source="ebay",
                    source_url="x", brand="Apple",
                    model_name="iphone 14 pro", category="phones",
                    condition="good", price=300, shipping=0,
                    fair_value=400, expected_resale=600,
                    net_profit=80, roi=0.27, confidence=0.5,
                    liquidity=0.5, score=0.5, risk_flags=[],
                    comp_source="active", comp_count=5,
                    match_quality=0.85, match_details="",
                    comp_evidence=[], why_passed="",
                    penalties_applied=[], status="approved",
                    decision="approved", lifecycle_stage="sold",
                    is_mock=False, dedupe_key=f"d{i}",
                    engine_version="v15.5.3",
                )
                s.add(cand)
                s.flush()
                purchase = PurchaseRecordRow(
                    candidate_id=cand.id, purchased_at=_utcnow(),
                    actual_purchase_price=300,
                    predicted_resale=600, predicted_profit=80,
                    predicted_roi=0.27, predicted_confidence=0.5,
                )
                s.add(purchase)
                s.flush()
                sale = SaleRecordRow(
                    purchase_id=purchase.id, sale_status="sold",
                    actual_sale_price=price,
                )
                s.add(sale)

    def test_unlocked_lookup_excludes_locked_and_unknown(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        from app.pipeline import _lookup_own_outcomes
        i = _id(storage_gb=256, carrier="unlocked")
        outcomes = _lookup_own_outcomes(i)
        assert sorted(outcomes) == [580.0, 600.0, 620.0]
        # Locked sales not included
        assert 420.0 not in outcomes
        assert 430.0 not in outcomes
        # Unknown-carrier sales not included
        assert 500.0 not in outcomes
        assert 510.0 not in outcomes

    def test_locked_lookup_excludes_unlocked(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        from app.pipeline import _lookup_own_outcomes
        i = _id(storage_gb=256, carrier="at&t")
        outcomes = _lookup_own_outcomes(i)
        assert sorted(outcomes) == [420.0, 430.0]

    def test_unknown_lookup_excludes_unlocked(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        from app.pipeline import _lookup_own_outcomes
        i = _id(storage_gb=256, carrier="")
        outcomes = _lookup_own_outcomes(i)
        assert sorted(outcomes) == [500.0, 510.0]

    def test_storage_still_separates_in_lookup(self, tmp_path, monkeypatch):
        """Sanity carry-over from v15.5.2: 128GB candidate must not match
        256GB sales even within the same carrier bucket."""
        self._seed(tmp_path, monkeypatch)
        from app.pipeline import _lookup_own_outcomes
        i = _id(storage_gb=128, carrier="unlocked")
        outcomes = _lookup_own_outcomes(i)
        # No 128GB sales were seeded
        assert outcomes == []


# ── 4. Other categories preserved ──────────────────────────────────

class TestOtherCategoriesPreserved:
    def test_laptop_valuation_identity_key_includes_chip_ram_storage(self):
        from app.models import NormalizedIdentity
        i = NormalizedIdentity(
            brand="Apple", model="macbook pro 14", category="laptops",
            cpu="m2 pro", ram_gb=16, storage_gb=512,
            charger_included=True, condition="good",
        )
        # Different chip generation → different identity key
        i2 = NormalizedIdentity(
            brand="Apple", model="macbook pro 14", category="laptops",
            cpu="m1 pro", ram_gb=16, storage_gb=512,
            charger_included=True, condition="good",
        )
        assert i.valuation_identity_key != i2.valuation_identity_key

    def test_shoe_valuation_identity_key_includes_size(self):
        from app.models import NormalizedIdentity
        a = NormalizedIdentity(
            brand="Nike", model="air jordan 1", category="shoes",
            size="10", condition="new",
        )
        b = NormalizedIdentity(
            brand="Nike", model="air jordan 1", category="shoes",
            size="11", condition="new",
        )
        assert a.valuation_identity_key != b.valuation_identity_key
