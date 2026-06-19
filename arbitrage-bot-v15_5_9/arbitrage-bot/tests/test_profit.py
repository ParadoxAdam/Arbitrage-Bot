"""Tests for the profit calculator."""
from app.models import Listing, _utcnow
from app.pricing.profit import calculate_profit


def _listing(price=100.0, shipping=10.0):
    return Listing(
        id="t1", source="test", source_url="http://x", source_item_id="t1",
        title="Test Item", category="shoes", price=price, shipping=shipping,
        scraped_at=_utcnow(),
    )


def test_profit_positive():
    pb = calculate_profit(_listing(price=200, shipping=15), expected_resale=400)
    assert pb.gross_revenue == 400
    assert pb.purchase == 200
    assert pb.inbound_shipping == 15
    assert pb.resale_fee > 0
    assert pb.payment_fee > 0
    assert pb.net_profit < 400 - 200 - 15
    assert pb.net_profit > 0
    assert pb.roi > 0


def test_profit_negative_when_overpriced():
    pb = calculate_profit(_listing(price=500), expected_resale=400)
    assert pb.net_profit < 0
    assert pb.roi < 0


def test_fees_scale_with_revenue():
    a = calculate_profit(_listing(), expected_resale=100)
    b = calculate_profit(_listing(), expected_resale=1000)
    assert b.resale_fee > a.resale_fee
    assert b.payment_fee > a.payment_fee


def test_zero_outbound_override():
    pb = calculate_profit(_listing(price=100), expected_resale=200, outbound_shipping=0)
    assert pb.outbound_shipping == 0
