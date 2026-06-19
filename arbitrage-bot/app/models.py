"""
All Pydantic (in-memory) and SQLAlchemy (persistence) models.

Timezone policy: every datetime is UTC-aware (datetime.now(UTC)).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field
from sqlalchemy import (String, Float, Boolean, DateTime, JSON, Text,
                        Integer, ForeignKey)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

UTC = timezone.utc


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ═══════════════════════════════════════════════════════════════════
# Pydantic models (pipeline in-memory)
# ═══════════════════════════════════════════════════════════════════

class Listing(BaseModel):
    """Raw listing as ingested from a source adapter."""
    id: str
    source: str                        # "ebay", "mock", etc.
    source_item_id: str = ""           # original item ID on the source platform
    source_url: str
    title: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: str = "other"
    spec: dict[str, Any] = Field(default_factory=dict)
    condition: Optional[str] = None
    price: float
    shipping: float = 0.0
    currency: str = "USD"
    location: Optional[str] = None
    seller: Optional[str] = None
    seller_rating: Optional[float] = None
    is_auction: bool = False
    pickup_only: bool = False
    scraped_at: datetime = Field(default_factory=_utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return self.price + (self.shipping or 0.0)


class NormalizedIdentity(BaseModel):
    """
    Structured product identity extracted by normalizers.
    This is what comp matching uses — not raw title strings.
    """
    brand: str = ""
    model: str = ""
    category: str = "other"
    # Category-specific fields
    size: Optional[str] = None              # shoes
    sku: Optional[str] = None               # shoes style code
    colorway: Optional[str] = None          # shoes / phones
    storage_gb: Optional[int] = None        # phones / laptops
    carrier: Optional[str] = None           # phones
    ram_gb: Optional[int] = None            # laptops
    cpu: Optional[str] = None               # laptops
    screen_size: Optional[str] = None       # laptops
    gpu: Optional[str] = None               # laptops
    charger_included: Optional[bool] = None # laptops
    condition: str = "good"

    @property
    def comp_key(self) -> str:
        """
        Primary key for comp table lookups.
        Category-aware: laptops include chip/RAM/storage so M1 Pro 16/512
        doesn't get bucketed with M5 24/1TB. Phones include storage.
        Shoes stay broad (size/SKU matters for matching, not bucketing).
        """
        base = f"{self.category}|{self.brand.lower()}|{self.model.lower()}"
        if self.category == "laptops":
            cpu = (self.cpu or "").lower().strip()
            ram = self.ram_gb or "?"
            storage = self.storage_gb or "?"
            return f"{base}|{cpu}|{ram}|{storage}"
        if self.category == "phones":
            storage = self.storage_gb or "?"
            return f"{base}|{storage}"
        return base

    @property
    def valuation_identity_key(self) -> str:
        """
        Strict identity key used ONLY for matching historical resale outcomes
        (own_outcomes lookup) and any future P&L grouping.

        v15.5.3: separate from comp_key on purpose — comp_key drives eBay
        comp matching (where carrier may be unknown on the comp side and
        we still want to compare). own outcomes need a stricter bucket so
        unlocked, locked, and unknown-carrier sales aren't blended.

        Carrier bucket rules:
          - empty/None       → "unknown"
          - contains 'unlocked' → "unlocked"
          - anything else    → "locked:<carrier_lowercased>"

        For laptops the key includes the comp_key plus charger status when known.
        For shoes the key includes size when known.
        """
        base = f"{self.category}|{self.brand.lower()}|{self.model.lower()}"
        if self.category == "phones":
            storage = self.storage_gb or "?"
            carrier_l = (self.carrier or "").lower().strip()
            if not carrier_l:
                carrier_bucket = "unknown"
            elif "unlocked" in carrier_l:
                carrier_bucket = "unlocked"
            else:
                carrier_bucket = f"locked:{carrier_l}"
            return f"{base}|{storage}|{carrier_bucket}"
        if self.category == "laptops":
            cpu = (self.cpu or "").lower().strip()
            ram = self.ram_gb or "?"
            storage = self.storage_gb or "?"
            charger = ("y" if self.charger_included else
                       "n" if self.charger_included is False else "?")
            return f"{base}|{cpu}|{ram}|{storage}|{charger}"
        if self.category == "shoes":
            size = (self.size or "?")
            sku = (self.sku or "?").lower()
            return f"{base}|{size}|{sku}"
        return base

    @property
    def search_query(self) -> str:
        """Search string used to fetch comps from eBay (less strict than comp_key)."""
        parts = [self.brand, self.model]
        if self.category == "laptops" and self.cpu:
            parts.append(self.cpu)
        if self.category == "phones" and self.storage_gb:
            parts.append(f"{self.storage_gb}GB")
        return " ".join(p for p in parts if p).strip()

    @property
    def spec_dict(self) -> dict[str, Any]:
        """Spec fields as dict, excluding None values."""
        fields = ["size", "sku", "colorway", "storage_gb", "carrier",
                  "ram_gb", "cpu", "screen_size", "gpu", "charger_included"]
        return {f: getattr(self, f) for f in fields if getattr(self, f) is not None}


class CompMatch(BaseModel):
    """Result from the comp engine with match quality metadata."""
    model_config = {"arbitrary_types_allowed": True}

    fair_value: float
    expected_resale: float
    confidence: float
    sample_size: int
    liquidity: float
    source: str                   # "sold" or "active"
    match_quality: float          # 0..1 — how well comps match this item
    match_details: str = ""       # human-readable match explanation
    comp_evidence: list[dict] = Field(default_factory=list)   # [{price, title}]
    # v15.5 — Valuation Engine v2 output (filled by app.valuation.engine).
    # When None, downstream readers fall back to the v1 fields above.
    valuation: dict | None = None


class Opportunity(BaseModel):
    listing: Listing
    identity: NormalizedIdentity
    fair_value: float
    expected_resale: float
    fees: float
    net_profit: float
    roi: float
    confidence: float
    liquidity: float
    risk_flags: list[str] = Field(default_factory=list)
    score: float = 0.0
    comp_source: str = "unknown"
    comp_count: int = 0
    match_quality: float = 0.0
    match_details: str = ""
    comp_evidence: list[dict] = Field(default_factory=list)
    # v15.5 — Valuation Engine v2 (full breakdown dict, see Valuation.to_dict)
    valuation: dict | None = None


# ═══════════════════════════════════════════════════════════════════
# SQLAlchemy models (persistence)
# ═══════════════════════════════════════════════════════════════════

class Base(DeclarativeBase):
    pass


class ScanRunRow(Base):
    """One execution of the pipeline."""
    __tablename__ = "scan_runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    sources_used: Mapped[list] = mapped_column(JSON, default=list)
    queries_run: Mapped[list] = mapped_column(JSON, default=list)
    listings_found: Mapped[int] = mapped_column(Integer, default=0)
    candidates_found: Mapped[int] = mapped_column(Integer, default=0)
    alerts_sent: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="running")

    # Comp engine tier stats (added v14)
    comp_scanned: Mapped[int] = mapped_column(Integer, default=0)
    comp_scored: Mapped[int] = mapped_column(Integer, default=0)
    comp_exact: Mapped[int] = mapped_column(Integer, default=0)
    comp_partial: Mapped[int] = mapped_column(Integer, default=0)
    comp_broad_rejected: Mapped[int] = mapped_column(Integer, default=0)
    comp_no_comps: Mapped[int] = mapped_column(Integer, default=0)
    comp_weak_match: Mapped[int] = mapped_column(Integer, default=0)

    # Engine version (added v15.4) — lets analytics filter pre/post hygiene fix
    engine_version: Mapped[str] = mapped_column(String, default="unknown", index=True)


class ListingRow(Base):
    """Raw listing as persisted."""
    __tablename__ = "listings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String)
    source_item_id: Mapped[str] = mapped_column(String, index=True)
    source_url: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    brand: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column("model", String, nullable=True)
    category: Mapped[str] = mapped_column(String, default="other")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    condition: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    shipping: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String, default="USD")
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    seller: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    seller_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_auction: Mapped[bool] = mapped_column(Boolean, default=False)
    pickup_only: Mapped[bool] = mapped_column(Boolean, default=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scan_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String, index=True, unique=True)

    # v15.4 — recheck policy fields
    last_scored_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True)
    last_seen_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_near_miss: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class NormalizedListingRow(Base):
    """Normalized identity for a listing."""
    __tablename__ = "normalized_listings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    brand: Mapped[str] = mapped_column(String)
    model_name: Mapped[str] = mapped_column("model", String)
    category: Mapped[str] = mapped_column(String)
    condition: Mapped[str] = mapped_column(String)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    comp_key: Mapped[str] = mapped_column(String, index=True)
    # v15.5.3: stricter identity key for own_outcomes lookup. Includes
    # the carrier bucket (unlocked / locked:<x> / unknown) for phones so
    # historical sales of unlocked vs locked phones don't get mixed.
    # Nullable so old rows continue to work; populated on new rows.
    valuation_identity_key: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True,
    )


class CompSnapshotRow(Base):
    """
    Snapshot of comp data used for a specific opportunity.
    One row per opportunity, persisting which prices/titles were used at scoring time.
    """
    __tablename__ = "comp_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True, index=True)
    comp_key: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)          # "sold" or "active"
    prices: Mapped[list] = mapped_column(JSON)
    titles: Mapped[list] = mapped_column(JSON, default=list)
    median_price: Mapped[float] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer)
    match_quality: Mapped[float] = mapped_column(Float, default=0.0)
    match_details: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    engine_version: Mapped[str] = mapped_column(String, default="unknown", index=True)


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    scan_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True)
    fair_value: Mapped[float] = mapped_column(Float)
    expected_resale: Mapped[float] = mapped_column(Float)
    net_profit: Mapped[float] = mapped_column(Float)
    roi: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    liquidity: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    comp_source: Mapped[str] = mapped_column(String)
    comp_count: Mapped[int] = mapped_column(Integer, default=0)
    match_quality: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)

    # v15.4 — engine version + persisted failure reasons
    engine_version: Mapped[str] = mapped_column(String, default="unknown", index=True)
    became_candidate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # JSON list of failure-reason codes (see decisions.FAILURE_REASONS).
    # Empty if the opportunity passed review.
    failure_reasons: Mapped[list] = mapped_column(JSON, default=list)

    # v15.5 — valuation engine v2 fields (additive, nullable for backward compat)
    valuation_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    valuation_method: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    valuation_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    conservative_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    optimistic_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valuation_warnings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    valuation_breakdown_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # v15.5.1 — explicit v1 / v2 separation. The legacy `expected_resale`
    # column above stores whichever number was used for profit math.
    # These two columns preserve the original v1 and v2 estimates for audit.
    v1_expected_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    v2_expected_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class ReviewCandidateRow(Base):
    __tablename__ = "review_candidates"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.id"), nullable=True)
    title: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(String)
    brand: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column("model", String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    condition: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    shipping: Mapped[float] = mapped_column(Float)
    fair_value: Mapped[float] = mapped_column(Float)
    expected_resale: Mapped[float] = mapped_column(Float)
    net_profit: Mapped[float] = mapped_column(Float)
    roi: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    liquidity: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list)
    comp_source: Mapped[str] = mapped_column(String)
    comp_count: Mapped[int] = mapped_column(Integer, default=0)
    match_quality: Mapped[float] = mapped_column(Float, default=0.0)
    match_details: Mapped[str] = mapped_column(Text, default="")
    comp_evidence: Mapped[list] = mapped_column(JSON, default=list)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_passed: Mapped[str] = mapped_column(Text)
    penalties_applied: Mapped[list] = mapped_column(JSON, default=list)

    # Legacy status: pending | approved | rejected (kept for backward compat)
    status: Mapped[str] = mapped_column(String, default="pending")

    # NEW: Structured decision (current state)
    decision: Mapped[str] = mapped_column(String, default="pending")
    decision_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)

    # NEW: Lifecycle stage (independent of decision)
    # none | purchased | listed | sold | closed
    lifecycle_stage: Mapped[str] = mapped_column(String, default="none")

    # NEW: Watchlist flag (orthogonal — decision can be 'pending' but watchlisted)
    watchlist: Mapped[bool] = mapped_column(Boolean, default=False)

    # NEW: Mock flag — for excluding from analytics
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
    dedupe_key: Mapped[str] = mapped_column(String, index=True)

    # v15.4 — engine version stamp
    engine_version: Mapped[str] = mapped_column(String, default="unknown", index=True)

    # v15.5 — valuation engine v2 fields (additive, nullable for backward compat)
    valuation_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    valuation_method: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    valuation_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    conservative_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    optimistic_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valuation_warnings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    valuation_breakdown_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # v15.5.1 — explicit v1 / v2 separation
    v1_expected_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    v2_expected_resale: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class ReviewDecisionEventRow(Base):
    """
    Immutable audit log of every decision change on a candidate.
    Enables analytics on decision history (e.g. "moved from approved -> rejected later").
    """
    __tablename__ = "review_decision_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("review_candidates.id"), index=True)
    previous_decision: Mapped[str] = mapped_column(String)
    new_decision: Mapped[str] = mapped_column(String)
    reason_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)


class PurchaseRecordRow(Base):
    """One per approved candidate that actually got bought."""
    __tablename__ = "purchase_records"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("review_candidates.id"), index=True, unique=True)

    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_purchase_price: Mapped[float] = mapped_column(Float)
    tax_paid: Mapped[float] = mapped_column(Float, default=0.0)
    inbound_shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)
    repair_cost: Mapped[float] = mapped_column(Float, default=0.0)
    misc_buy_costs: Mapped[float] = mapped_column(Float, default=0.0)

    marketplace_purchased_from: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    purchase_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    purchase_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    seller_risk_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Snapshot of predictions at purchase time (for predicted-vs-actual)
    predicted_resale: Mapped[float] = mapped_column(Float)
    predicted_profit: Mapped[float] = mapped_column(Float)
    predicted_roi: Mapped[float] = mapped_column(Float)
    predicted_confidence: Mapped[float] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)


class SaleRecordRow(Base):
    """One per purchase that gets listed for resale (and possibly sold)."""
    __tablename__ = "sale_records"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    purchase_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_records.id"), index=True, unique=True)

    # listed | sold | unsold | returned | abandoned
    sale_status: Mapped[str] = mapped_column(String, default="listed")

    listed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    sale_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    sale_platform: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    actual_sale_price: Mapped[float] = mapped_column(Float, default=0.0)
    outbound_shipping_cost: Mapped[float] = mapped_column(Float, default=0.0)
    selling_fees: Mapped[float] = mapped_column(Float, default=0.0)
    payment_processing_fees: Mapped[float] = mapped_column(Float, default=0.0)
    return_costs: Mapped[float] = mapped_column(Float, default=0.0)

    days_to_sell: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    final_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)


class PnlSnapshotRow(Base):
    """
    Computed P&L per purchase.
    Recomputed when purchase or sale records change.
    Stores predicted-vs-actual deltas for analytics.
    """
    __tablename__ = "pnl_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    purchase_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_records.id"), index=True, unique=True)

    # Actuals (zero until sale completes)
    actual_gross_proceeds: Mapped[float] = mapped_column(Float, default=0.0)
    actual_total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    actual_net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    actual_roi: Mapped[float] = mapped_column(Float, default=0.0)

    # Predicted (snapshotted at purchase time)
    predicted_resale: Mapped[float] = mapped_column(Float, default=0.0)
    predicted_profit: Mapped[float] = mapped_column(Float, default=0.0)
    predicted_roi: Mapped[float] = mapped_column(Float, default=0.0)

    # Errors (actual - predicted)
    resale_error: Mapped[float] = mapped_column(Float, default=0.0)
    profit_error: Mapped[float] = mapped_column(Float, default=0.0)
    roi_error: Mapped[float] = mapped_column(Float, default=0.0)

    is_finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)


class AlertLogRow(Base):
    """Track which alerts were sent and when."""
    __tablename__ = "alert_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True)
    review_candidate_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("review_candidates.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String)       # "telegram", "discord", "console"
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class LifecycleEventRow(Base):
    """
    Audit log of every lifecycle transition on a candidate's trade.
    Captures purchased / listed / relisted / sold / liquidated / etc.
    Independent of decision events — this tracks the trade side.
    """
    __tablename__ = "lifecycle_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("review_candidates.id"), index=True)
    purchase_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("purchase_records.id"), nullable=True)
    sale_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sale_records.id"), nullable=True)

    event_type: Mapped[str] = mapped_column(String)
    previous_stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    new_stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Optional event payload (e.g. price at sale, platform, days_to_sell)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)


class QueryPerformanceRow(Base):
    """
    Per-query performance per scan run. Lets us see which queries are
    productive (lots of new listings, exact matches, candidates) vs
    expensive (lots of fetches, no candidates).
    """
    __tablename__ = "query_performance"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_run_id: Mapped[int] = mapped_column(
        ForeignKey("scan_runs.id"), index=True)
    query_terms: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String)

    # Funnel counts (added v15.1: split raw fetch from filtering)
    raw_returned: Mapped[int] = mapped_column(Integer, default=0)
    negative_filtered: Mapped[int] = mapped_column(Integer, default=0)
    listings_fetched: Mapped[int] = mapped_column(Integer, default=0)  # post-filter
    new_listings: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, default=0)

    # Comp engine stats
    listings_scored: Mapped[int] = mapped_column(Integer, default=0)
    exact_match_total: Mapped[int] = mapped_column(Integer, default=0)
    partial_match_total: Mapped[int] = mapped_column(Integer, default=0)
    broad_rejected_total: Mapped[int] = mapped_column(Integer, default=0)

    # Outcome stats
    candidates_created: Mapped[int] = mapped_column(Integer, default=0)
    alerts_sent: Mapped[int] = mapped_column(Integer, default=0)

    # v15.4 — per-query failure reason counters
    # Counts how many scored listings were dropped at review for each reason.
    failed_profit: Mapped[int] = mapped_column(Integer, default=0)
    failed_roi: Mapped[int] = mapped_column(Integer, default=0)
    failed_score: Mapped[int] = mapped_column(Integer, default=0)
    failed_confidence: Mapped[int] = mapped_column(Integer, default=0)
    failed_match_quality: Mapped[int] = mapped_column(Integer, default=0)
    failed_active_only: Mapped[int] = mapped_column(Integer, default=0)
    failed_battery_health: Mapped[int] = mapped_column(Integer, default=0)
    failed_risk_flags: Mapped[int] = mapped_column(Integer, default=0)
    failed_comp_pool: Mapped[int] = mapped_column(Integer, default=0)
    failed_no_comps: Mapped[int] = mapped_column(Integer, default=0)
    failed_other: Mapped[int] = mapped_column(Integer, default=0)

    # Engine version for this row (added v15.4)
    engine_version: Mapped[str] = mapped_column(String, default="unknown", index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)
