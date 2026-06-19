"""
Regression tests for v15.4.3 — comp pool pollution fixes.

Driven by Adam's Apr 27 DB dump showing the iPhone 13 Pro 128GB Silver
opportunity #583 used these polluted comps:

  £104.70  iPhone 13 Pro 128GB Sierra Blue Cosmetic Damage (Reparable)
  £115.10  iPhone 13 Pro 128GB Unlocked
  £139.99  Apple iPhone 13 Pro 128GB Graphite Unlocked works with damage
  £141.10  Apple iPhone 13 Pro 128GB Silver Unlocked 86% BH Read Description
  £146.30  Apple iPhone 13 Pro 128GB Silver Unlocked Broken Back
  £125.49  iPhone 13 Pro 128GB Unlocked Faulty Spares Repair
  £94.30   iPhone 13 Pro Max 128GB Unlocked Alpine Green For Parts iCloud Free

…producing a £109 estimated resale on a phone that legitimately resells
at £300+. These tests guard against regression.
"""
import pytest
from app.pricing.ebay_comps import (
    _matches_negative_keyword, _below_price_floor, _resolve_price_floor,
)


# ── Damage keyword detection ───────────────────────────────────────

class TestDamageKeywords:
    def test_filters_cosmetic_damage(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro 128GB Sierra Blue Cosmetic Damage (Reparable)",
            "phones",
        )
        assert kw is not None

    def test_filters_works_with_damage(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro - 128GB - Graphite (Unlocked) works with damage",
            "phones",
        )
        assert kw is not None

    def test_filters_read_description(self):
        """Common eBay seller code for 'there's a problem'."""
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro - 128GB - Silver (Unlocked) - 86% BH - Read Description",
            "phones",
        )
        assert kw is not None

    def test_filters_broken_back_no_space(self):
        """Title has '(Unlocked)Broken Back' with no space before Broken."""
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro - 128GB - Silver (Unlocked)Broken Back",
            "phones",
        )
        assert kw is not None

    def test_filters_low_battery_health(self):
        """Listings advertising 7x-8x% battery health are degraded."""
        for bh in [" 78% ", " 82% ", " 86% ", " 89% "]:
            title = f"Apple iPhone 13 Pro 128GB Unlocked{bh}BH Tested Working"
            kw = _matches_negative_keyword(title, "phones")
            assert kw is not None, f"failed to filter {title!r}"

    def test_keeps_legit_listing(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro 128GB Unlocked Excellent Condition", "phones",
        )
        assert kw is None


# ── Word-boundary matching ──────────────────────────────────────────

class TestWordBoundaryMatching:
    def test_case_does_not_match_casey(self):
        """Old bug: 'case' substring matched 'casey'. New regex uses \\b."""
        kw = _matches_negative_keyword(
            "iPhone 13 Pro Owned by Casey 128GB Unlocked", "phones",
        )
        assert kw is None

    def test_case_matches_with_punctuation(self):
        """'Phone Case)' should still match 'case'."""
        kw = _matches_negative_keyword(
            "iPhone 13 Pro Phone Case) Black", "phones",
        )
        assert kw == "case"

    def test_broken_matches_after_paren(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro 128GB (Unlocked)Broken Screen", "phones",
        )
        assert kw == "broken"


# ── Product-aware price floors ──────────────────────────────────────

class TestProductAwareFloor:
    def test_iphone_13_pro_floor_is_200(self):
        floor = _resolve_price_floor(
            "phones", "iPhone 13 Pro 128GB unlocked", "",
        )
        assert floor >= 200.0

    def test_iphone_15_pro_max_floor_is_higher(self):
        floor = _resolve_price_floor(
            "phones", "iPhone 15 Pro Max 256GB", "",
        )
        # Pro Max is more expensive, floor should be higher
        assert floor >= 450.0

    def test_iphone_15_pro_max_higher_than_13_pro(self):
        f13 = _resolve_price_floor("phones", "iPhone 13 Pro 128GB", "")
        f15 = _resolve_price_floor("phones", "iPhone 15 Pro Max 256GB", "")
        assert f15 > f13

    def test_macbook_pro_14_has_higher_floor(self):
        floor = _resolve_price_floor("laptops", "MacBook Pro 14 M2 Pro", "")
        assert floor >= 600.0

    def test_generic_phone_uses_base_floor(self):
        """An unknown phone model should fall back to the £80 base."""
        floor = _resolve_price_floor("phones", "Nokia 3210", "")
        assert floor == 80.0

    def test_below_floor_uses_query_context(self):
        """The £115 'iPhone 13 Pro 128GB Unlocked' from Adam's DB —
        clean title but implausibly cheap for the model."""
        result = _below_price_floor(
            115.10, "phones",
            query="iPhone 13 Pro 128GB unlocked",
            title="iPhone 13 Pro 128GB Unlocked",
        )
        assert result is True

    def test_legit_price_passes_floor(self):
        """A real iPhone 13 Pro at £320 passes."""
        result = _below_price_floor(
            320.0, "phones",
            query="iPhone 13 Pro 128GB unlocked",
            title="Apple iPhone 13 Pro 128GB Unlocked Excellent",
        )
        assert result is False


# ── Full pipeline reproduction of opp #583 ─────────────────────────

class TestAdamsBugReproduction:
    """End-to-end: feed the exact polluted comp pool, verify it gets
    rejected so no fake £109 estimate gets persisted."""

    def test_polluted_comp_pool_rejected(self, monkeypatch):
        from unittest.mock import MagicMock
        from app.pricing.ebay_comps import fetch_comp_items

        polluted = {
            "itemSummaries": [
                {"itemId": "1", "title": "iPhone 13 Pro 128GB Sierra Blue Cosmetic Damage (Reparable)",
                 "price": {"value": "104.70"}},
                {"itemId": "2", "title": "iPhone 13 Pro 128GB Unlocked",
                 "price": {"value": "115.10"}},
                {"itemId": "3", "title": "Apple iPhone 13 Pro - 128GB - Graphite (Unlocked) works with damage",
                 "price": {"value": "139.99"}},
                {"itemId": "4", "title": "Apple iPhone 13 Pro - 128GB - Silver (Unlocked) - 86% BH - Read Description",
                 "price": {"value": "141.10"}},
                {"itemId": "5", "title": "Apple iPhone 13 Pro - 128GB - Silver (Unlocked)Broken Back",
                 "price": {"value": "146.30"}},
                {"itemId": "6", "title": "iPhone 13 Pro 128GB Unlocked Faulty Spares Repair Non Genuine Screen Battery",
                 "price": {"value": "125.49"}},
                {"itemId": "7", "title": "Apple iPhone 13 Pro Max 128GB Unlocked Alpine Green, For Parts, iCloud Free.",
                 "price": {"value": "94.30"}},
                # Add 2 more bad ones to hit raw_count threshold
                {"itemId": "8", "title": "iPhone 13 Pro 128GB cracked screen",
                 "price": {"value": "150.00"}},
                {"itemId": "9", "title": "iPhone 13 Pro 128GB faulty needs repair",
                 "price": {"value": "180.00"}},
            ]
        }

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = polluted

        monkeypatch.setattr("app.pricing.ebay_comps.httpx.get",
                            lambda *a, **kw: fake_resp)
        monkeypatch.setattr(
            "app.pricing.ebay_comps.get_access_token",
            lambda: "fake-token",
        )
        monkeypatch.setattr("app.config.settings.ebay_client_id", "fake")

        result = fetch_comp_items(
            "iPhone 13 Pro 128GB unlocked", "phones", "9355",
        )

        # Either the pool is fully rejected, or every surviving comp is
        # at least the product-aware floor (£200 for iPhone 13 Pro).
        if not result.pool_rejected:
            for item in result.items:
                assert item.price >= 200.0, (
                    f"Junk comp survived: £{item.price} {item.title}"
                )

        # In the realistic case, 9 polluted comps → 0 valid → pool rejected
        assert result.pool_rejected is True or len(result.items) <= 1
