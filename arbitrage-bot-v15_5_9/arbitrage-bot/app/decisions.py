"""
Decision codes, lifecycle stages, and sale statuses.

A candidate has TWO orthogonal state fields:
  - decision: what you said when reviewing it
  - lifecycle_stage: where it is in the buy/sell flow
"""

# ── Decisions ───────────────────────────────────────────────────────

PENDING = "pending"
APPROVED = "approved"
PASSED_NO_ACTION = "passed_no_action"
WATCHLIST = "watchlist"
NEEDS_MORE_INFO = "needs_more_info"

REJECTED_MOCK = "rejected_mock"
REJECTED_BAD_MATCH = "rejected_bad_match"
REJECTED_BAD_CONDITION = "rejected_bad_condition"
REJECTED_TOO_RISKY = "rejected_too_risky"
REJECTED_MARGIN_NOT_REAL = "rejected_margin_not_real"
REJECTED_NOT_MY_CATEGORY = "rejected_not_my_category"
REJECTED_INSUFFICIENT_CONFIDENCE = "rejected_insufficient_confidence"
REJECTED_OTHER = "rejected_other"

ALL_DECISIONS = [
    PENDING, APPROVED, PASSED_NO_ACTION, WATCHLIST, NEEDS_MORE_INFO,
    REJECTED_MOCK, REJECTED_BAD_MATCH, REJECTED_BAD_CONDITION,
    REJECTED_TOO_RISKY, REJECTED_MARGIN_NOT_REAL,
    REJECTED_NOT_MY_CATEGORY, REJECTED_INSUFFICIENT_CONFIDENCE,
    REJECTED_OTHER,
]

REJECTION_DECISIONS = {
    REJECTED_MOCK, REJECTED_BAD_MATCH, REJECTED_BAD_CONDITION,
    REJECTED_TOO_RISKY, REJECTED_MARGIN_NOT_REAL,
    REJECTED_NOT_MY_CATEGORY, REJECTED_INSUFFICIENT_CONFIDENCE,
    REJECTED_OTHER,
}

ACTIVE_DECISIONS = {APPROVED, WATCHLIST, NEEDS_MORE_INFO}
ANALYTICS_EXCLUDED = {REJECTED_MOCK}

DECISION_LABELS = {
    PENDING: "Pending",
    APPROVED: "Approved",
    PASSED_NO_ACTION: "Passed (no action)",
    WATCHLIST: "Watchlist",
    NEEDS_MORE_INFO: "Needs more info",
    REJECTED_MOCK: "Mock data",
    REJECTED_BAD_MATCH: "Bad spec match",
    REJECTED_BAD_CONDITION: "Bad condition",
    REJECTED_TOO_RISKY: "Too risky",
    REJECTED_MARGIN_NOT_REAL: "Margin isn't real",
    REJECTED_NOT_MY_CATEGORY: "Not my category",
    REJECTED_INSUFFICIENT_CONFIDENCE: "Insufficient confidence",
    REJECTED_OTHER: "Other reason",
}


# ── Lifecycle stages ────────────────────────────────────────────────

STAGE_NONE = "none"
STAGE_PURCHASED = "purchased"
STAGE_LISTED = "listed"
STAGE_SOLD = "sold"
STAGE_CLOSED = "closed"

ALL_STAGES = [STAGE_NONE, STAGE_PURCHASED, STAGE_LISTED, STAGE_SOLD, STAGE_CLOSED]

STAGE_LABELS = {
    STAGE_NONE: "Not bought",
    STAGE_PURCHASED: "Purchased",
    STAGE_LISTED: "Listed for resale",
    STAGE_SOLD: "Sold",
    STAGE_CLOSED: "Closed",
}


# ── Sale statuses ───────────────────────────────────────────────────
# More nuanced than v12: not every unsold item is a write-off.

SALE_LISTED = "listed"
SALE_SOLD = "sold"
SALE_UNSOLD_HOLDING = "unsold_still_holding"  # Listed but not sold; you still have it
SALE_RELISTED = "relisted"                     # Listed again after first attempt failed
SALE_LIQUIDATED = "liquidated"                 # Fire-sold below estimate to recover something
SALE_RETURNED = "returned"                     # Buyer returned it
SALE_WRITTEN_OFF = "written_off"               # Officially counted as a loss
SALE_ABANDONED = "abandoned"                   # Lost / damaged / can't sell

ALL_SALE_STATUSES = [
    SALE_LISTED, SALE_SOLD, SALE_UNSOLD_HOLDING, SALE_RELISTED,
    SALE_LIQUIDATED, SALE_RETURNED, SALE_WRITTEN_OFF, SALE_ABANDONED,
]

SALE_STATUS_LABELS = {
    SALE_LISTED: "Listed for sale",
    SALE_SOLD: "Sold",
    SALE_UNSOLD_HOLDING: "Unsold (still holding)",
    SALE_RELISTED: "Relisted",
    SALE_LIQUIDATED: "Liquidated",
    SALE_RETURNED: "Returned by buyer",
    SALE_WRITTEN_OFF: "Written off",
    SALE_ABANDONED: "Abandoned",
}

# Statuses that indicate the trade is finalized (P&L is computable)
FINALIZED_SALE_STATUSES = {
    SALE_SOLD, SALE_LIQUIDATED, SALE_RETURNED,
    SALE_WRITTEN_OFF, SALE_ABANDONED,
}

# Statuses that mean "we have proceeds" — counted toward gross
SALE_HAS_PROCEEDS = {SALE_SOLD, SALE_LIQUIDATED}

# Statuses where the item is still in inventory (unrealized)
INVENTORY_STATUSES = {SALE_LISTED, SALE_UNSOLD_HOLDING, SALE_RELISTED}


# ── Lifecycle event types (for audit trail) ─────────────────────────

EVENT_PURCHASED = "purchased"
EVENT_LISTED = "listed"
EVENT_RELISTED = "relisted"
EVENT_SOLD = "sold"
EVENT_LIQUIDATED = "liquidated"
EVENT_RETURNED = "returned"
EVENT_WRITTEN_OFF = "written_off"
EVENT_ABANDONED = "abandoned"
EVENT_UNSOLD_HOLDING = "unsold_still_holding"
EVENT_UPDATED = "updated"

ALL_LIFECYCLE_EVENTS = [
    EVENT_PURCHASED, EVENT_LISTED, EVENT_RELISTED, EVENT_SOLD,
    EVENT_LIQUIDATED, EVENT_RETURNED, EVENT_WRITTEN_OFF,
    EVENT_ABANDONED, EVENT_UNSOLD_HOLDING, EVENT_UPDATED,
]


# ── Failure reasons (v15.4) ─────────────────────────────────────────
# Persisted on each opportunity that doesn't become a review candidate.
# Matches the qstats failed_* fields one-to-one.

FAIL_PROFIT = "profit_below_threshold"
FAIL_ROI = "roi_below_threshold"
FAIL_SCORE = "score_below_threshold"
FAIL_CONFIDENCE = "confidence_below_threshold"
FAIL_MATCH_QUALITY = "match_quality_below_threshold"
FAIL_ACTIVE_ONLY = "active_comps_only"
FAIL_BATTERY_HEALTH = "missing_battery_health"
FAIL_RISK_FLAGS = "critical_risk_flags"
FAIL_COMP_POOL = "comp_pool_rejected"
FAIL_NO_COMPS = "no_comps_found"
FAIL_OTHER = "other"

ALL_FAILURE_REASONS = [
    FAIL_PROFIT, FAIL_ROI, FAIL_SCORE, FAIL_CONFIDENCE, FAIL_MATCH_QUALITY,
    FAIL_ACTIVE_ONLY, FAIL_BATTERY_HEALTH, FAIL_RISK_FLAGS, FAIL_COMP_POOL,
    FAIL_NO_COMPS, FAIL_OTHER,
]

# Mapping from failure reason -> qstats counter field name
FAILURE_REASON_TO_QSTATS_FIELD = {
    FAIL_PROFIT: "failed_profit",
    FAIL_ROI: "failed_roi",
    FAIL_SCORE: "failed_score",
    FAIL_CONFIDENCE: "failed_confidence",
    FAIL_MATCH_QUALITY: "failed_match_quality",
    FAIL_ACTIVE_ONLY: "failed_active_only",
    FAIL_BATTERY_HEALTH: "failed_battery_health",
    FAIL_RISK_FLAGS: "failed_risk_flags",
    FAIL_COMP_POOL: "failed_comp_pool",
    FAIL_NO_COMPS: "failed_no_comps",
    FAIL_OTHER: "failed_other",
}
