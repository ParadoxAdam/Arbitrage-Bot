from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Versioning ───────────────────────────────────────────────────────
# APP_VERSION is the human-visible release name shown in the UI.
# VALUATION_VERSION is stamped onto every scan_run, opportunity,
# comp_snapshot, review_candidate, and query_performance row, so analytics
# can filter pre-/post-fix data cleanly. Bump it whenever scoring or comping
# logic changes in a way that invalidates older valuations.
APP_VERSION = "v15.5.9"
# v15.5.9 is a purely additive UI/analytics release (target buy price /
# negotiation feature). Scoring, comping, and valuation logic are
# unchanged, so VALUATION_VERSION stays at v15.5.8 — analytics keep
# grouping pre/post-15.5.9 rows together.
VALUATION_VERSION = "v15.5.8"

# Backwards-compat alias — older code reads CURRENT_ENGINE_VERSION
CURRENT_ENGINE_VERSION = VALUATION_VERSION


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "sqlite:///./arbitrage.db"
    scan_interval_minutes: int = 10
    currency: str = "GBP"              # GBP | USD | EUR
    currency_symbol: str = "£"
    use_mock_comps: bool = False       # False = always go live to eBay for comps

    # v15.5.1: When True (default), profit/ROI/score use the v2 valuation
    # expected_resale. Set to False to revert profit math to the v1 comp_match
    # estimate (v2 still computed and shown on the dashboard for comparison).
    # The default is True so the v2 engine is the source of truth.
    use_v2_for_profit: bool = True

    # Versions stamped onto persisted records
    app_version: str = APP_VERSION
    valuation_version: str = VALUATION_VERSION
    # Legacy alias kept so existing code keeps working
    engine_version: str = VALUATION_VERSION

    # ── Category toggles (v15.4.6) ──────────────────────────────────
    # Disable a whole category to skip every query for it. Phones-only
    # for the validation phase. Override via .env, e.g.:
    #   CATEGORIES_ENABLED=phones
    # or
    #   CATEGORIES_ENABLED=phones,shoes
    categories_enabled: str = "phones"

    # ── Alert thresholds (Telegram/Discord — high bar) ──────────────
    # These only fire when we have sold comp data and high confidence.
    min_profit: float = 50.0
    min_roi: float = 0.25
    min_score: float = 0.60
    min_confidence: float = 0.50

    # ── Review thresholds (shadow mode — lower bar) ─────────────────
    # Candidates that pass these go to the review table for manual inspection.
    review_min_profit: float = 40.0
    review_min_roi: float = 0.20
    review_min_score: float = 0.20
    review_min_confidence: float = 0.15

    # Alert channels
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""

    # eBay API
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace: str = "EBAY_GB"

    # Fee model (UK eBay defaults)
    resale_fee_pct: float = 0.13
    payment_fee_pct: float = 0.02
    payment_fee_flat: float = 0.30
    default_outbound_shipping: float = 6.0

    # ── Recheck policy (v15.4) ──────────────────────────────────────
    # How long before a previously-scored listing should be re-evaluated
    recheck_after_hours: float = 24.0
    # Materially-different price triggers immediate rescore (% delta)
    recheck_price_change_pct: float = 0.05    # 5%

    # ── Negotiation feature (v15.5.9) ───────────────────────────────
    # A failed listing is flagged as "negotiable" if the discount needed
    # to make it review-grade is within EITHER limit (laxer wins).
    # Both are configurable; defaults match typical UK eBay best-offer
    # behaviour (5–15% discounts are routine, larger ones are rare).
    negotiation_max_discount_pct: float = 0.15
    negotiation_max_discount_abs: float = 30.0

    @property
    def enabled_categories(self) -> set[str]:
        """Parse the categories_enabled string into a set."""
        return {c.strip().lower() for c in self.categories_enabled.split(",")
                if c.strip()}


settings = Settings()
