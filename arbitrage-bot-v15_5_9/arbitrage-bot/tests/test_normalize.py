"""Tests for category normalizers."""
from app.models import Listing, _utcnow
from app.normalize import normalize


def _listing(title, category, brand="", model="", raw=None):
    return Listing(
        id="t1", source="test", source_url="http://x", source_item_id="t1",
        title=title, brand=brand, model=model, category=category,
        price=100, shipping=0, scraped_at=_utcnow(),
        raw=raw or {},
    )


def test_shoes_size_from_title():
    l = _listing("Nike Air Jordan 1 Size 10 DS", "shoes", brand="Nike")
    identity = normalize(l)
    assert identity.size == "10"
    assert identity.condition == "new"  # DS = deadstock = new


def test_shoes_sku_from_title():
    l = _listing("Jordan 1 DZ5485-612 Size 9", "shoes", brand="Nike")
    identity = normalize(l)
    assert identity.sku == "DZ5485-612"


def test_phones_storage_from_title():
    l = _listing("iPhone 14 Pro 256GB Unlocked", "phones", brand="Apple")
    identity = normalize(l)
    assert identity.storage_gb == 256
    assert identity.carrier == "unlocked"


def test_phones_locked_carrier():
    l = _listing("iPhone 14 Pro 128GB AT&T", "phones", brand="Apple")
    identity = normalize(l)
    assert identity.carrier == "at&t"


def test_laptops_specs_from_title():
    l = _listing("MacBook Pro 14 M2 Pro 16GB RAM 512GB SSD", "laptops",
                 brand="Apple")
    identity = normalize(l)
    assert identity.cpu is not None
    assert "m2" in identity.cpu.lower()
    assert "pro" in identity.cpu.lower()
    assert identity.ram_gb == 16
    assert identity.storage_gb == 512


def test_laptops_charger_detection():
    l = _listing("MacBook Pro 14 w/ charger", "laptops", brand="Apple")
    identity = normalize(l)
    assert identity.charger_included is True

    l2 = _listing("MacBook Pro 14 no charger", "laptops", brand="Apple")
    identity2 = normalize(l2)
    assert identity2.charger_included is False


def test_identity_comp_key():
    l = _listing("Nike Dunk Low", "shoes", brand="Nike", model="Dunk Low")
    identity = normalize(l)
    assert identity.comp_key == "shoes|nike|dunk low"


def test_identity_spec_dict():
    l = _listing("iPhone 15 Pro 256GB Unlocked", "phones",
                 brand="Apple", model="iPhone 15 Pro")
    identity = normalize(l)
    spec = identity.spec_dict
    assert "storage_gb" in spec
    assert spec["storage_gb"] == 256
