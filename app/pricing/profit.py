"""
Profit calculator. Pure functions, fully unit-testable.
"""
from __future__ import annotations
from dataclasses import dataclass
from ..config import settings
from ..models import Listing


@dataclass
class ProfitBreakdown:
    purchase: float
    inbound_shipping: float
    outbound_shipping: float
    resale_fee: float          # marketplace cut (e.g. eBay 13%)
    payment_fee: float         # payment processor (e.g. 2.9% + $0.30)
    estimated_tax: float       # sales tax on the purchase if applicable
    total_cost: float          # everything you spend
    gross_revenue: float       # what you sell it for
    net_profit: float          # gross_revenue - total_cost
    roi: float                 # net_profit / (purchase + inbound + tax)


def calculate_profit(
    listing: Listing,
    expected_resale: float,
    *,
    outbound_shipping: float | None = None,
    sales_tax_pct: float = 0.0,
) -> ProfitBreakdown:
    """
    Calculate full profit breakdown for flipping a listing.

    Args:
        listing: The item we'd buy.
        expected_resale: What we expect to sell it for.
        outbound_shipping: Override shipping cost to the buyer. Uses default if None.
        sales_tax_pct: Sales tax rate on the purchase (e.g. 0.08 for 8%).
    """
    purchase = listing.price
    inbound = listing.shipping or 0.0
    outbound = (
        outbound_shipping
        if outbound_shipping is not None
        else settings.default_outbound_shipping
    )

    # Buyer-side sales tax on the purchase (if applicable)
    estimated_tax = round(purchase * sales_tax_pct, 2)

    # Resale platform fees on the gross sale price
    resale_fee = round(expected_resale * settings.resale_fee_pct, 2)
    payment_fee = round(
        expected_resale * settings.payment_fee_pct + settings.payment_fee_flat, 2
    )

    total_cost = purchase + inbound + estimated_tax + outbound + resale_fee + payment_fee
    net = expected_resale - total_cost
    cost_basis = purchase + inbound + estimated_tax
    roi = (net / cost_basis) if cost_basis > 0 else 0.0

    return ProfitBreakdown(
        purchase=round(purchase, 2),
        inbound_shipping=round(inbound, 2),
        outbound_shipping=round(outbound, 2),
        resale_fee=resale_fee,
        payment_fee=payment_fee,
        estimated_tax=estimated_tax,
        total_cost=round(total_cost, 2),
        gross_revenue=round(expected_resale, 2),
        net_profit=round(net, 2),
        roi=round(roi, 4),
    )
