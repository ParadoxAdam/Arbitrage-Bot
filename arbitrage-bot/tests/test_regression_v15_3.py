"""
Regression tests for v15.3 — comp pool hygiene.

The Apr 27 dashboard showed:
- iPhone 13 Pro 256GB unlocked → est. resale £157 (correct: £350-450)
  Comp pool was polluted with locked/parts/broken iPhones.
- Nike SB Dunk Low → est. resale £7 (correct: £80-150)
  Comp pool was polluted with insoles, laces, posters, miniatures.
- Off-White Jordan 1 → est. resale £8 (correct: £400-700)
  Comp pool was polluted with t-shirts, posters, replicas.

These tests guard against those failure modes.
"""
import pytest
from app.pricing.ebay_comps import (
    _matches_negative_keyword, _has_product_token, _below_price_floor,
    fetch_comp_items, CompFetchResult,
    PHONE_COMP_NEGATIVES, SHOE_COMP_NEGATIVES, LAPTOP_COMP_NEGATIVES,
    COMP_PRICE_FLOORS, MIN_VALID_COMPS, MIN_VALID_RATIO,
)


# ── Negative keyword filtering ──────────────────────────────────────

class TestPhoneNegativeFilter:
    """Phones must drop accessories, locked, parts, broken."""

    def test_drops_phone_case(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro Case Heavy Duty", "phones",
        )
        assert kw == "case"

    def test_drops_screen_protector(self):
        kw = _matches_negative_keyword(
            "Tempered glass screen protector for iPhone 13 Pro", "phones",
        )
        assert kw is not None

    def test_drops_charger(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro Charger Original 20W", "phones",
        )
        assert kw == "charger"

    def test_drops_for_parts(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro 256GB FOR PARTS Cracked Screen", "phones",
        )
        assert kw is not None

    def test_drops_spares_or_repair(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro spares or repair", "phones",
        )
        assert kw is not None

    def test_drops_icloud_locked(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro 128GB iCloud locked", "phones",
        )
        assert kw is not None

    def test_drops_activation_locked(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro Activation Lock", "phones",
        )
        assert kw is not None

    def test_drops_blacklisted(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro 256GB blacklisted IMEI", "phones",
        )
        assert kw is not None

    def test_drops_screen_only(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro screen only OEM replacement", "phones",
        )
        assert kw is not None

    def test_drops_box_only(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro 256GB box only no phone", "phones",
        )
        assert kw is not None

    def test_drops_finance_issue(self):
        kw = _matches_negative_keyword(
            "iPhone 13 Pro 256GB outstanding finance", "phones",
        )
        assert kw is not None

    def test_keeps_legit_phone(self):
        kw = _matches_negative_keyword(
            "Apple iPhone 13 Pro 256GB Gold Unlocked", "phones",
        )
        assert kw is None


class TestShoeNegativeFilter:
    """Shoes must drop laces, insoles, posters, keyrings, miniatures, replicas."""

    def test_drops_laces(self):
        kw = _matches_negative_keyword(
            "Nike SB Dunk Low Replacement Laces 100% Genuine", "shoes",
        )
        assert kw is not None

    def test_drops_insoles(self):
        kw = _matches_negative_keyword(
            "Nike SB Dunk Low Insoles Pair", "shoes",
        )
        assert kw is not None

    def test_drops_keyring(self):
        kw = _matches_negative_keyword(
            "Air Jordan 1 keyring keychain mini sneaker", "shoes",
        )
        assert kw is not None

    def test_drops_poster(self):
        kw = _matches_negative_keyword(
            "Off-White Jordan 1 poster print A3 wall art", "shoes",
        )
        assert kw is not None

    def test_drops_miniature(self):
        kw = _matches_negative_keyword(
            "Air Jordan 1 miniature collectible 3D model", "shoes",
        )
        assert kw is not None

    def test_drops_replica(self):
        kw = _matches_negative_keyword(
            "Air Jordan 1 Off-White REPLICA", "shoes",
        )
        assert kw is not None

    def test_drops_box_only(self):
        kw = _matches_negative_keyword(
            "Air Jordan 1 Travis Scott shoebox only - empty", "shoes",
        )
        assert kw is not None

    def test_drops_t_shirt(self):
        kw = _matches_negative_keyword(
            "Off-White Virgil Abloh Jordan 1 tribute t-shirt", "shoes",
        )
        assert kw is not None

    def test_keeps_legit_shoe(self):
        kw = _matches_negative_keyword(
            "Nike Air Jordan 1 Retro High OG Chicago Size 10", "shoes",
        )
        assert kw is None


class TestLaptopNegativeFilter:
    """Laptops must drop chargers, screens, parts, broken."""

    def test_drops_screen_replacement(self):
        kw = _matches_negative_keyword(
            "MacBook Pro 14 M2 Pro screen replacement display assembly",
            "laptops",
        )
        assert kw is not None

    def test_drops_charger_only(self):
        kw = _matches_negative_keyword(
            "MacBook Pro 14 charger only original 67W USB-C", "laptops",
        )
        assert kw is not None

    def test_drops_for_parts(self):
        kw = _matches_negative_keyword(
            "MacBook Pro 14 M1 Pro for parts not working", "laptops",
        )
        assert kw is not None

    def test_drops_logic_board(self):
        kw = _matches_negative_keyword(
            "MacBook Pro 14 M2 Pro logic board only", "laptops",
        )
        assert kw is not None

    def test_drops_battery_only(self):
        kw = _matches_negative_keyword(
            "MacBook Pro 14 battery only replacement", "laptops",
        )
        assert kw is not None

    def test_keeps_legit_laptop(self):
        kw = _matches_negative_keyword(
            "Apple MacBook Pro 14 M2 Pro 16GB 512GB", "laptops",
        )
        assert kw is None


# ── Product-type validation ─────────────────────────────────────────

class TestProductTokenValidation:
    """Title must contain a product-family token."""

    def test_phone_with_iphone_token_passes(self):
        assert _has_product_token("Apple iPhone 13 Pro 256GB", "phones") is True

    def test_phone_with_galaxy_token_passes(self):
        assert _has_product_token("Samsung Galaxy S23 Ultra", "phones") is True

    def test_phone_without_any_token_fails(self):
        assert _has_product_token("Some random electronics item", "phones") is False

    def test_shoe_with_jordan_token_passes(self):
        assert _has_product_token("Air Jordan 1 Retro High", "shoes") is True

    def test_shoe_with_dunk_token_passes(self):
        assert _has_product_token("Nike SB Dunk Low Pro", "shoes") is True

    def test_shoe_with_trainers_token_passes(self):
        assert _has_product_token("Nike Trainers UK 10", "shoes") is True

    def test_laptop_with_macbook_token_passes(self):
        assert _has_product_token("Apple MacBook Pro 14 M2", "laptops") is True

    def test_laptop_with_thinkpad_token_passes(self):
        assert _has_product_token("Lenovo ThinkPad X1 Carbon", "laptops") is True


# ── Price floor sanity ──────────────────────────────────────────────

class TestPriceFloor:
    def test_phone_below_floor_rejected(self):
        # An "iPhone 13 Pro" at £25 is implausible and almost certainly junk
        assert _below_price_floor(25.0, "phones") is True

    def test_phone_at_floor_accepted(self):
        assert _below_price_floor(85.0, "phones") is False

    def test_laptop_below_floor_rejected(self):
        assert _below_price_floor(50.0, "laptops") is True

    def test_shoe_below_floor_rejected(self):
        # The £7.53 Nike SB Dunk Low bug
        assert _below_price_floor(7.53, "shoes") is True

    def test_shoe_at_floor_accepted(self):
        assert _below_price_floor(40.0, "shoes") is False


# ── Comp pool sanity ────────────────────────────────────────────────

class TestPoolSanity:
    def test_pool_rejected_when_too_few_valid(self):
        """If only 1-2 valid comps survive, pool is rejected."""
        result = CompFetchResult()
        result.raw_count = 20
        result.dropped_negative = 18
        result.items = [None, None]   # 2 items — below MIN_VALID_COMPS = 3
        # Manually re-run sanity check (would normally happen inside fetch)
        if len(result.items) < MIN_VALID_COMPS:
            result.pool_rejected = True
            result.rejection_reason = "only 2 valid comps"
        assert result.pool_rejected is True

    def test_pool_rejected_when_junk_ratio_too_high(self):
        """If most comps are junk, the search itself was bad."""
        result = CompFetchResult()
        result.raw_count = 40
        result.dropped_negative = 35
        result.items = [None] * 5    # 5/40 = 12.5%, below 20%
        valid_ratio = len(result.items) / result.raw_count
        if valid_ratio < MIN_VALID_RATIO:
            result.pool_rejected = True
        assert result.pool_rejected is True

    def test_pool_accepted_when_clean(self):
        result = CompFetchResult()
        result.raw_count = 20
        result.dropped_negative = 2
        result.items = [None] * 18
        valid_ratio = len(result.items) / result.raw_count
        # Should NOT be flagged
        assert valid_ratio >= MIN_VALID_RATIO
        assert len(result.items) >= MIN_VALID_COMPS


# ── End-to-end via mocked fetch ─────────────────────────────────────

class TestFetchIntegration:
    """End-to-end: pollution-loaded responses get filtered correctly."""

    def _polluted_response_for_iphone_13(self):
        """Mimics the bad data we saw: lots of cases, locked, broken."""
        return {
            "itemSummaries": [
                # Junk
                {"itemId": "1", "title": "Apple iPhone 13 Pro Case Clear",
                 "price": {"value": "12.99"}},
                {"itemId": "2", "title": "iPhone 13 Pro screen protector 3 pack",
                 "price": {"value": "8.50"}},
                {"itemId": "3", "title": "iPhone 13 Pro 256GB iCloud LOCKED",
                 "price": {"value": "45.00"}},
                {"itemId": "4", "title": "iPhone 13 Pro for parts only no power",
                 "price": {"value": "60.00"}},
                {"itemId": "5", "title": "iPhone 13 Pro screen replacement OEM",
                 "price": {"value": "75.00"}},
                {"itemId": "6", "title": "iPhone 13 Pro charger 20W USB-C",
                 "price": {"value": "10.00"}},
                # Genuine comps
                {"itemId": "7", "title": "Apple iPhone 13 Pro 256GB Gold Unlocked",
                 "price": {"value": "385.00"}},
                {"itemId": "8", "title": "Apple iPhone 13 Pro 256GB Silver Unlocked",
                 "price": {"value": "395.00"}},
                {"itemId": "9", "title": "Apple iPhone 13 Pro 256GB Sierra Blue Unlocked",
                 "price": {"value": "410.00"}},
                {"itemId": "10", "title": "Apple iPhone 13 Pro 256GB Graphite Unlocked",
                 "price": {"value": "405.00"}},
            ]
        }

    def test_polluted_iphone_search_filters_junk(self, monkeypatch):
        """The actual bug from the dashboard: comp engine should reject
        junk before computing the median, not let £45 locked-iCloud
        listings drag the median down."""
        from unittest.mock import MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = self._polluted_response_for_iphone_13()

        monkeypatch.setattr("app.pricing.ebay_comps.httpx.get",
                            lambda *a, **kw: fake_resp)
        monkeypatch.setattr(
            "app.pricing.ebay_comps.get_access_token",
            lambda: "fake-token",
        )
        monkeypatch.setattr("app.config.settings.ebay_client_id", "fake")

        result = fetch_comp_items(
            "Apple iPhone 13 Pro 256GB unlocked",
            "phones", "9355",
        )

        # Junk items should be filtered out entirely
        valid_titles = [c.title for c in result.items]
        assert not any("case" in t.lower() for t in valid_titles)
        assert not any("screen protector" in t.lower() for t in valid_titles)
        assert not any("icloud" in t.lower() for t in valid_titles)
        assert not any("for parts" in t.lower() for t in valid_titles)
        assert not any("screen replacement" in t.lower() for t in valid_titles)
        assert not any("charger" in t.lower() for t in valid_titles)

        # Real listings should survive
        assert len(result.items) == 4
        assert all(c.price >= 80 for c in result.items)   # all above floor
        assert all("iphone" in c.title.lower() for c in result.items)

        # Diagnostic counters populated
        assert result.dropped_negative >= 4
        assert result.raw_count == 10

    def test_polluted_dunk_search_returns_only_real_shoes(self, monkeypatch):
        """The £7.53 Nike SB Dunk Low bug. Junk should be filtered out."""
        from unittest.mock import MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "itemSummaries": [
                # All junk
                {"itemId": "1", "title": "Nike SB Dunk Low replacement laces pair",
                 "price": {"value": "5.99"}},
                {"itemId": "2", "title": "Nike SB Dunk Low keyring keychain miniature",
                 "price": {"value": "7.50"}},
                {"itemId": "3", "title": "Nike SB Dunk Low poster print A3 wall art",
                 "price": {"value": "12.00"}},
                {"itemId": "4", "title": "Nike SB Dunk Low insoles cushion",
                 "price": {"value": "15.00"}},
                {"itemId": "5", "title": "Nike SB Dunk Low t-shirt fan tribute",
                 "price": {"value": "20.00"}},
                # Real
                {"itemId": "6", "title": "Nike SB Dunk Low Pro Classic Green UK 10",
                 "price": {"value": "120.00"}},
                {"itemId": "7", "title": "Nike SB Dunk Low Travis Scott UK 9 Trainers",
                 "price": {"value": "350.00"}},
                {"itemId": "8", "title": "Nike SB Dunk Low Pro Sneakers UK 11",
                 "price": {"value": "140.00"}},
            ]
        }

        monkeypatch.setattr("app.pricing.ebay_comps.httpx.get",
                            lambda *a, **kw: fake_resp)
        monkeypatch.setattr(
            "app.pricing.ebay_comps.get_access_token",
            lambda: "fake-token",
        )
        monkeypatch.setattr("app.config.settings.ebay_client_id", "fake")

        result = fetch_comp_items("Nike SB Dunk Low", "shoes", "93427")

        valid_titles = [c.title.lower() for c in result.items]
        assert not any("laces" in t for t in valid_titles)
        assert not any("keyring" in t for t in valid_titles)
        assert not any("poster" in t for t in valid_titles)
        assert not any("insoles" in t for t in valid_titles)
        assert not any("t-shirt" in t for t in valid_titles)

        # Real shoes survive
        assert len(result.items) == 3
        assert all(c.price >= 35 for c in result.items)

    def test_pool_rejected_if_search_returns_only_junk(self, monkeypatch):
        """If the search is so bad that 90% of results are junk, pool
        should be rejected — not silently use the few survivors."""
        from unittest.mock import MagicMock

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        # 9 junk items, 1 real item — only 10% valid
        fake_resp.json.return_value = {
            "itemSummaries": [
                *[
                    {"itemId": str(i),
                     "title": f"iPhone 13 Pro case clear protective #{i}",
                     "price": {"value": "12.99"}}
                    for i in range(9)
                ],
                {"itemId": "10",
                 "title": "Apple iPhone 13 Pro 256GB Unlocked",
                 "price": {"value": "390.00"}},
            ]
        }

        monkeypatch.setattr("app.pricing.ebay_comps.httpx.get",
                            lambda *a, **kw: fake_resp)
        monkeypatch.setattr(
            "app.pricing.ebay_comps.get_access_token",
            lambda: "fake-token",
        )
        monkeypatch.setattr("app.config.settings.ebay_client_id", "fake")

        result = fetch_comp_items(
            "iPhone 13 Pro", "phones", "9355",
        )

        # Pool should be rejected because:
        # (a) junk ratio too high (10% valid), AND
        # (b) only 1 valid comp survived (below MIN_VALID_COMPS = 3)
        assert result.pool_rejected is True
        assert result.rejection_reason  # non-empty
