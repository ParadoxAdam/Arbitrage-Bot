"""
Pipeline: fetch -> dedupe -> normalize -> comp -> score -> persist -> review/alert.

Persists scan runs, raw listings, normalized listings, opportunities,
and alert state.
"""
from __future__ import annotations
import logging
from datetime import datetime
from .config import settings, CURRENT_ENGINE_VERSION
from .models import (
    Listing, ScanRunRow, ListingRow, NormalizedListingRow,
    CompSnapshotRow, OpportunityRow, _utcnow,
)
from .db import session_scope
from .sources.base import BaseSource
from .sources.mock_marketplace import MockMarketplace
from .normalize import normalize
from .pricing.comps import estimate
from .pricing.profit import calculate_profit
from .scoring import score_opportunity, CRITICAL_FLAGS, detect_risk_flags
from .alerts import send_alert
from .review import store_review_candidate
from .dedupe import exact_dedupe_key, is_duplicate
from .queries import get_queries

log = logging.getLogger("pipeline")


def _build_sources() -> list[BaseSource]:
    sources: list[BaseSource] = []
    if settings.env == "dev":
        sources.append(MockMarketplace())
    if settings.ebay_client_id:
        from .sources.ebay_api import EbayBrowseSource
        sources.append(EbayBrowseSource(throttle_seconds=1.0))
        log.info("eBay source enabled")
    else:
        log.info("eBay source skipped (no credentials)")
    return sources


def _should_rescore_existing(existing: ListingRow, new_price: float,
                              now: datetime) -> tuple[bool, str]:
    """
    Decide if a previously-seen listing should be rescored.
    Returns (should_rescore, reason).
    """
    # Watchlisted candidates: always rescore
    from .models import ReviewCandidateRow
    from .decisions import WATCHLIST
    with session_scope() as s:
        wl = s.query(ReviewCandidateRow).filter_by(
            listing_id=existing.id,
        ).filter(ReviewCandidateRow.decision == WATCHLIST).first()
        if wl:
            return True, "watchlisted"

    # Near-miss flag: always rescore (we want to catch these the moment
    # something changes)
    if existing.is_near_miss:
        return True, "near_miss"

    # Material price change?
    if existing.last_seen_price and new_price > 0:
        delta = abs(new_price - existing.last_seen_price) / max(
            existing.last_seen_price, 1.0,
        )
        if delta >= settings.recheck_price_change_pct:
            return True, f"price_changed_{delta*100:.1f}%"

    # Stale: hasn't been scored recently
    if existing.last_scored_at:
        age = existing.last_scored_at
        if age.tzinfo is None:
            from datetime import timezone as _tz
            age = age.replace(tzinfo=_tz.utc)
        hours_since = (now - age).total_seconds() / 3600
        if hours_since >= settings.recheck_after_hours:
            return True, f"stale_{hours_since:.0f}h"

    return False, "fresh"


def _persist_listing(listing: Listing, scan_run_id: int,
                     ) -> tuple[int | None, str]:
    """
    Persist a raw listing.
    Returns (row_id, mode) where mode is one of:
      "new"     — first time seen, must be scored
      "rescore" — already seen, but recheck policy says rescore
      "skip"    — already seen and still fresh, skip
    Row ID is non-None for both "new" and "rescore" modes.
    """
    key = exact_dedupe_key(listing)
    with session_scope() as s:
        existing = s.query(ListingRow).filter_by(dedupe_key=key).first()
        if existing:
            should, reason = _should_rescore_existing(
                existing, listing.price, _utcnow(),
            )
            if not should:
                return None, "skip"

            # Update price/seen tracking on existing row before rescore
            existing.last_seen_price = listing.price
            existing.scraped_at = listing.scraped_at
            log.info("  rescoring existing listing %d (%s): %s",
                     existing.id, reason, listing.title[:50])
            return existing.id, "rescore"

        row = ListingRow(
            source=listing.source,
            source_item_id=listing.source_item_id,
            source_url=listing.source_url,
            title=listing.title,
            brand=listing.brand,
            model_name=listing.model,
            category=listing.category,
            spec=listing.spec,
            condition=listing.condition,
            price=listing.price,
            shipping=listing.shipping,
            currency=listing.currency,
            location=listing.location,
            seller=listing.seller,
            seller_rating=listing.seller_rating,
            is_auction=listing.is_auction,
            pickup_only=listing.pickup_only,
            scraped_at=listing.scraped_at,
            scan_run_id=scan_run_id,
            dedupe_key=key,
            last_seen_price=listing.price,
        )
        s.add(row)
        s.flush()
        return row.id, "new"


def _persist_normalized(listing_row_id: int, identity, comp_key: str) -> None:
    with session_scope() as s:
        row = NormalizedListingRow(
            listing_id=listing_row_id,
            brand=identity.brand,
            model_name=identity.model,
            category=identity.category,
            condition=identity.condition,
            spec=identity.spec_dict,
            comp_key=comp_key,
            # v15.5.3: stricter key for own_outcomes — includes carrier bucket
            valuation_identity_key=identity.valuation_identity_key,
        )
        s.add(row)


def _persist_opportunity(listing_row_id: int, scan_run_id: int, op,
                         identity_comp_key: str, *,
                         became_candidate: bool = False,
                         failure_reasons: list | None = None) -> int:
    """Persist opportunity AND its comp snapshot.
    Stamps current engine_version, persisted failure reasons (v15.4),
    and v15.5 valuation breakdown."""
    val = op.valuation or {}
    with session_scope() as s:
        row = OpportunityRow(
            listing_id=listing_row_id,
            scan_run_id=scan_run_id,
            fair_value=op.fair_value,
            expected_resale=op.expected_resale,
            net_profit=op.net_profit,
            roi=op.roi,
            confidence=op.confidence,
            liquidity=op.liquidity,
            score=op.score,
            risk_flags=op.risk_flags,
            comp_source=op.comp_source,
            comp_count=op.comp_count,
            match_quality=op.match_quality,
            engine_version=CURRENT_ENGINE_VERSION,
            became_candidate=became_candidate,
            failure_reasons=failure_reasons or [],
            # v15.5
            valuation_version=val.get("valuation_version"),
            valuation_method=val.get("valuation_method"),
            valuation_confidence=val.get("valuation_confidence"),
            conservative_resale=val.get("conservative_resale"),
            optimistic_resale=val.get("optimistic_resale"),
            valuation_warnings=val.get("warnings") or None,
            valuation_breakdown_json=val or None,
            # v15.5.1 — explicit v1/v2 separation. expected_resale above is
            # whichever feeds profit math; these preserve the originals.
            v1_expected_resale=val.get("v1_expected_resale"),
            v2_expected_resale=val.get("expected_resale"),
        )
        s.add(row)
        s.flush()
        opp_id = row.id

        # Persist the comp snapshot used for this opportunity
        prices = [e["price"] for e in (op.comp_evidence or [])]
        titles = [e["title"] for e in (op.comp_evidence or [])]
        if prices:
            from .models import CompSnapshotRow
            median_p = sorted(prices)[len(prices) // 2]
            snap = CompSnapshotRow(
                opportunity_id=opp_id,
                comp_key=identity_comp_key,
                source=op.comp_source,
                prices=prices,
                titles=titles,
                median_price=median_p,
                sample_size=op.comp_count,
                match_quality=op.match_quality,
                match_details=op.match_details,
                fetched_at=_utcnow(),
                engine_version=CURRENT_ENGINE_VERSION,
            )
            s.add(snap)

        return opp_id


def _passes_review(op) -> bool:
    has_critical = bool(set(op.risk_flags) & CRITICAL_FLAGS)
    return (
        not has_critical
        and op.net_profit >= settings.review_min_profit
        and op.roi >= settings.review_min_roi
        and op.score >= settings.review_min_score
        and op.confidence >= settings.review_min_confidence
    )


def _review_fail_reason(op) -> str:
    """Why didn't this opportunity make it onto the review list?"""
    critical = set(op.risk_flags) & CRITICAL_FLAGS
    if critical:
        return f"critical risk flag: {', '.join(critical)}"
    reasons = []
    if op.net_profit < settings.review_min_profit:
        reasons.append(
            f"profit {op.net_profit:.2f} < {settings.review_min_profit}"
        )
    if op.roi < settings.review_min_roi:
        reasons.append(
            f"ROI {op.roi*100:.1f}% < {settings.review_min_roi*100:.0f}%"
        )
    if op.score < settings.review_min_score:
        reasons.append(
            f"score {op.score:.2f} < {settings.review_min_score:.2f}"
        )
    if op.confidence < settings.review_min_confidence:
        reasons.append(
            f"confidence {op.confidence:.2f} < {settings.review_min_confidence:.2f}"
        )
    return "; ".join(reasons) if reasons else "passed review (would alert)"


def _is_genuine_near_miss(op) -> bool:
    """
    Decide whether a failed-review opportunity is close enough to count
    as a near-miss for recheck purposes.

    The bar must be high — too generous a definition causes the recheck
    policy to repeatedly rescan failures, undermining dedupe and burning
    API quota. v15.4.2 criteria:

      - No critical risk flags
      - Match quality at or above 0.5 (we trust the comps)
      - Profit within 80% of review_min_profit
      - ROI within 80% of review_min_roi  (added v15.4.2)
      - Score within 80% of review_min_score
      - Confidence within 80% of review_min_confidence

    Listings that fail with weak comps, critical flags, or numbers far
    below thresholds are NOT near-misses — they're genuine misses.
    The ROI check stops cash-profit-only matches: a £35 profit on a £500
    item is not "close" to a flippable margin.
    """
    if set(op.risk_flags) & CRITICAL_FLAGS:
        return False

    if op.match_quality < 0.5:
        return False

    # Profit: within 20% below threshold (£32 at £40 default)
    if op.net_profit < settings.review_min_profit * 0.80:
        return False

    # ROI: within 20% below threshold (16% at 20% default).
    # This stops listings with adequate cash profit but poor margins
    # from getting rescored repeatedly.
    if op.roi < settings.review_min_roi * 0.80:
        return False

    # Score within 20% below threshold
    if op.score < settings.review_min_score * 0.80:
        return False

    # Confidence within 20% below threshold
    if op.confidence < settings.review_min_confidence * 0.80:
        return False

    return True


def _failure_reason_codes(op) -> list[str]:
    """
    Structured list of failure reason codes — one entry per rule that failed.
    Used for persisted analytics and per-query failure breakdowns.
    """
    from .decisions import (
        FAIL_PROFIT, FAIL_ROI, FAIL_SCORE, FAIL_CONFIDENCE,
        FAIL_MATCH_QUALITY, FAIL_ACTIVE_ONLY, FAIL_BATTERY_HEALTH,
        FAIL_RISK_FLAGS,
    )
    codes: list[str] = []

    critical = set(op.risk_flags) & CRITICAL_FLAGS
    if critical:
        codes.append(FAIL_RISK_FLAGS)

    if op.net_profit < settings.review_min_profit:
        codes.append(FAIL_PROFIT)
    if op.roi < settings.review_min_roi:
        codes.append(FAIL_ROI)
    if op.score < settings.review_min_score:
        codes.append(FAIL_SCORE)
    if op.confidence < settings.review_min_confidence:
        codes.append(FAIL_CONFIDENCE)
    if op.match_quality < 0.5:
        codes.append(FAIL_MATCH_QUALITY)

    # Soft signals — flagged for analytics but don't block on their own
    if op.comp_source == "active":
        codes.append(FAIL_ACTIVE_ONLY)
    if "missing_battery_health" in op.risk_flags:
        codes.append(FAIL_BATTERY_HEALTH)

    return codes


def _bump_failure_qstats(qstats: dict | None, codes: list[str]) -> None:
    """Increment per-query counters for each failure code."""
    if qstats is None:
        return
    from .decisions import FAILURE_REASON_TO_QSTATS_FIELD
    for code in codes:
        field = FAILURE_REASON_TO_QSTATS_FIELD.get(code)
        if field and field in qstats:
            qstats[field] += 1


def _lookup_own_outcomes(identity) -> list[float]:
    """
    Best-effort lookup of past sold prices for the SAME product identity.

    v15.5.3 — matches on NormalizedListingRow.valuation_identity_key, which
    encodes brand + model + storage + carrier-bucket. This is STRICTER than
    comp_key:
      - "phones|apple|iphone 14 pro|256|unlocked"
      - "phones|apple|iphone 14 pro|256|locked:at&t"
      - "phones|apple|iphone 14 pro|256|unknown"
    are three different identities and won't be averaged together.

    Returns a list of actual_sale_price values where sale_status indicates
    proceeds were received. Quiet on any failure — the valuation engine
    handles an empty list explicitly via the own_outcomes_not_available
    warning.
    """
    try:
        from .models import (
            SaleRecordRow, PurchaseRecordRow, ReviewCandidateRow,
            NormalizedListingRow,
        )
        from .decisions import SALE_HAS_PROCEEDS
        target_key = identity.valuation_identity_key
        with session_scope() as s:
            q = (
                s.query(SaleRecordRow.actual_sale_price)
                .join(PurchaseRecordRow,
                      SaleRecordRow.purchase_id == PurchaseRecordRow.id)
                .join(ReviewCandidateRow,
                      PurchaseRecordRow.candidate_id == ReviewCandidateRow.id)
                .join(NormalizedListingRow,
                      NormalizedListingRow.listing_id == ReviewCandidateRow.listing_id)
                .filter(SaleRecordRow.sale_status.in_(SALE_HAS_PROCEEDS))
                .filter(ReviewCandidateRow.is_mock == False)  # noqa: E712
                # v15.5.3 — strict identity match, NOT comp_key
                .filter(NormalizedListingRow.valuation_identity_key == target_key)
            )
            return [r[0] for r in q.all() if r[0] and r[0] > 0]
    except Exception:
        return []


def _passes_alert(op) -> bool:
    has_critical = bool(set(op.risk_flags) & CRITICAL_FLAGS)
    return (
        not has_critical
        and op.comp_source == "sold"    # ENFORCE: alerts require sold comps
        and op.net_profit >= settings.min_profit
        and op.roi >= settings.min_roi
        and op.score >= settings.min_score
        and op.confidence >= settings.min_confidence
    )


def _start_scan(sources, queries) -> int:
    with session_scope() as s:
        row = ScanRunRow(
            started_at=_utcnow(),
            sources_used=[src.name for src in sources],
            queries_run=[q.terms for q in queries],
            status="running",
            engine_version=CURRENT_ENGINE_VERSION,
        )
        s.add(row)
        s.flush()
        return row.id


def _finish_scan(scan_id: int, counters: dict) -> None:
    from .pricing.comps import get_stats
    stats = get_stats()
    with session_scope() as s:
        row = s.query(ScanRunRow).filter_by(id=scan_id).first()
        if row:
            row.finished_at = _utcnow()
            row.listings_found = counters["listings"]
            row.candidates_found = counters["reviewed"]
            row.alerts_sent = counters["alerted"]
            row.status = "completed"
            row.comp_scanned = stats.scanned
            row.comp_scored = stats.scored
            row.comp_exact = stats.exact_match
            row.comp_partial = stats.partial_match
            row.comp_broad_rejected = stats.broad_rejected
            row.comp_no_comps = stats.no_comps
            row.comp_weak_match = stats.weak_match


def _process_listing(
    listing: Listing,
    scan_run_id: int,
    counters: dict,
    qstats: dict | None = None,
) -> None:
    """Full pipeline for one listing.

    `qstats` (when provided) accumulates per-query metrics that we persist
    after the scan completes.
    """
    if qstats is not None:
        qstats["listings_fetched"] += 1

    # 1. Dedupe (DB-backed) — now returns mode flag for recheck policy
    listing_row_id, mode = _persist_listing(listing, scan_run_id)
    if listing_row_id is None:
        if qstats is not None:
            qstats["duplicates_skipped"] += 1
        return  # fresh duplicate, skip

    if mode == "new":
        counters["listings"] += 1
        if qstats is not None:
            qstats["new_listings"] += 1
    # mode == "rescore": not counted as a new listing, but we DO continue
    # through the rest of the pipeline to update the score.

    # 2. Normalize
    identity = normalize(listing)
    if mode == "new":
        _persist_normalized(listing_row_id, identity, identity.comp_key)
    # On rescore we keep the existing normalized record; identity rarely changes.

    # 2b. Pre-comp critical-flag check (v15.4.8)
    # If the listing is multi-variant, suspicious-new, or otherwise
    # untradeable, skip it before burning API quota on comps.
    pre_flags = detect_risk_flags(listing, identity)
    pre_critical = set(pre_flags) & CRITICAL_FLAGS
    if pre_critical:
        log.info("  SKIP (critical pre-comp flag): %s — %s",
                 listing.title[:50], ", ".join(pre_critical))
        # Still update last_scored_at so we don't keep retrying every minute
        with session_scope() as s:
            row = s.query(ListingRow).filter_by(id=listing_row_id).first()
            if row:
                row.last_scored_at = _utcnow()
                row.is_near_miss = False
        return

    # 3. Comps + score
    from .pricing.comps import get_stats as _get_comp_stats
    pre = _get_comp_stats()
    pre_exact, pre_partial, pre_broad, pre_no_comps = (
        pre.exact_match, pre.partial_match,
        pre.broad_rejected, pre.no_comps,
    )

    comp = estimate(listing, identity)

    post = _get_comp_stats()
    if qstats is not None:
        qstats["exact_match_total"] += post.exact_match - pre_exact
        qstats["partial_match_total"] += post.partial_match - pre_partial
        qstats["broad_rejected_total"] += post.broad_rejected - pre_broad

    if not comp:
        # Distinguish pool-rejected from genuinely no comps for analytics
        no_comps_delta = post.no_comps - pre_no_comps
        if qstats is not None:
            if no_comps_delta:
                qstats["failed_no_comps"] += 1
            else:
                qstats["failed_comp_pool"] += 1
        # Update last_scored_at so we don't keep retrying every minute
        with session_scope() as s:
            row = s.query(ListingRow).filter_by(id=listing_row_id).first()
            if row:
                row.last_scored_at = _utcnow()
        log.info("  SKIP (no comps): %s", listing.title)
        return

    if qstats is not None:
        qstats["listings_scored"] += 1

    # v15.5.1 — pipeline reordered:
    #   1. detect risk flags (so valuation can see them)
    #   2. run Valuation Engine v2
    #   3. choose which expected_resale to use for profit (v2 if enabled,
    #      else v1 fallback)
    #   4. run profit + scoring with the chosen number
    #   5. apply confidence cap if v2 says the valuation is unreliable
    pre_risk_flags = detect_risk_flags(listing, identity)

    valuation_dict: dict | None = None
    try:
        from .valuation import value_listing
        # Best-effort own_outcomes lookup — keep this lightweight and
        # surface the result via warnings inside the engine.
        own_outcomes = _lookup_own_outcomes(identity)
        valuation_obj = value_listing(
            listing, identity, comp,
            risk_flags=pre_risk_flags,
            own_outcomes=own_outcomes,
        )
        valuation_dict = valuation_obj.to_dict()
    except Exception as e:
        log.warning("valuation engine threw: %s — using v1 only", e)
        from .config import VALUATION_VERSION
        valuation_dict = {
            "valuation_method": "engine_fallback_v1",
            "valuation_version": VALUATION_VERSION,
            "warnings": ["valuation_engine_fallback"],
            "expected_resale": comp.expected_resale,
            "v1_expected_resale": comp.expected_resale,
            "valuation_confidence": comp.confidence,
            "conservative_resale": round(comp.expected_resale * 0.92, 2),
            "optimistic_resale": round(comp.expected_resale * 1.08, 2),
        }

    # v15.5.1 — choose the expected_resale used downstream
    v1_estimate = comp.expected_resale
    v2_estimate = valuation_dict.get("expected_resale", v1_estimate) if valuation_dict else v1_estimate
    use_v2 = settings.use_v2_for_profit and v2_estimate and v2_estimate > 0
    headline_resale = v2_estimate if use_v2 else v1_estimate

    # If we're using v2, propagate the v2 estimate through the
    # CompMatch so downstream uses the same number consistently.
    if use_v2 and abs(v2_estimate - v1_estimate) > 0.01:
        # Mutate a shallow-replaced CompMatch — keep evidence intact
        comp = comp.model_copy(update={"expected_resale": v2_estimate})

    profit = calculate_profit(listing, headline_resale)
    op = score_opportunity(listing, identity, comp, profit)
    op.valuation = valuation_dict

    # v15.5.1 — Guardrail enforcement: reference anchors and suspicious
    # warnings must NOT trigger alerts. Cap confidence so the listing
    # falls below MIN_CONFIDENCE=0.50 and cannot pass the alert gate.
    if valuation_dict:
        method = valuation_dict.get("valuation_method", "")
        warnings = valuation_dict.get("warnings", []) or []
        anchor_driven = method == "anchor_driven_review_only"
        suspicious = ("valuation_suspicious_low" in warnings
                      or "valuation_suspicious_high" in warnings)
        if anchor_driven or suspicious:
            # Cap to 0.40 so MIN_CONFIDENCE=0.50 alert gate cannot pass.
            # Critically, we cap op.confidence which feeds _passes_alert.
            if op.confidence > 0.40:
                op.confidence = 0.40
            if "valuation_alert_blocked" not in op.risk_flags:
                op.risk_flags = list(op.risk_flags) + ["valuation_alert_blocked"]

    log.info(
        "  %s -> %s%.2f  roi=%.0f%%  score=%.2f  match=%.2f  "
        "comps=%s(%d)  flags=%s",
        listing.title[:40],
        settings.currency_symbol, op.net_profit,
        op.roi * 100, op.score, op.match_quality,
        op.comp_source, op.comp_count,
        op.risk_flags or "none",
    )

    became_candidate = _passes_review(op)
    failure_codes = [] if became_candidate else _failure_reason_codes(op)

    opportunity_id = _persist_opportunity(
        listing_row_id, scan_run_id, op, identity.comp_key,
        became_candidate=became_candidate,
        failure_reasons=failure_codes,
    )

    # Update last_scored_at and near-miss flag on the listing row
    # v15.4.1: only mark as near-miss if it genuinely came close — otherwise
    # the recheck policy rescans every failure and burns API quota.
    near_miss = (not became_candidate) and _is_genuine_near_miss(op)
    with session_scope() as s:
        row = s.query(ListingRow).filter_by(id=listing_row_id).first()
        if row:
            row.last_scored_at = _utcnow()
            row.is_near_miss = near_miss

    if became_candidate:
        candidate_id = store_review_candidate(
            op, listing_row_id, opportunity_id=opportunity_id,
        )
        counters["reviewed"] += 1
        if qstats is not None:
            qstats["candidates_created"] += 1

        if _passes_alert(op):
            send_alert(
                op,
                opportunity_id=opportunity_id,
                review_candidate_id=candidate_id,
            )
            counters["alerted"] += 1
            if qstats is not None:
                qstats["alerts_sent"] += 1
    else:
        # Didn't pass review — record in the diagnostic "top failed" list,
        # tagging whether it's a genuine near-miss (recheckable) vs just a
        # failed listing (informational only).
        from .pricing.comps import add_near_miss, NearMiss
        add_near_miss(NearMiss(
            title=listing.title,
            url=listing.source_url,
            price=listing.price,
            shipping=listing.shipping,
            expected_resale=op.expected_resale,
            net_profit=op.net_profit,
            roi=op.roi,
            score=op.score,
            confidence=op.confidence,
            match_quality=op.match_quality,
            comp_source=op.comp_source,
            comp_count=op.comp_count,
            category=listing.category,
            fail_reason=_review_fail_reason(op),
            is_genuine_near_miss=near_miss,
            # v15.5.4: propagate Valuation Engine v2 fields to the Top Failed tab
            v1_expected_resale=(valuation_dict or {}).get("v1_expected_resale"),
            v2_expected_resale=(valuation_dict or {}).get("expected_resale"),
            valuation_method=(valuation_dict or {}).get("valuation_method"),
            valuation_warnings=(valuation_dict or {}).get("warnings"),
            # v15.5.6: full breakdown so the Top Failed tab can render the
            # collapsible "Valuation breakdown" section identically to
            # the Review Queue cards.
            valuation_confidence=(valuation_dict or {}).get("valuation_confidence"),
            conservative_resale=(valuation_dict or {}).get("conservative_resale"),
            optimistic_resale=(valuation_dict or {}).get("optimistic_resale"),
            valuation_breakdown=valuation_dict,
            # v15.5.9: propagate structured failure codes and risk flags
            # so the negotiation analyser can categorise listings into the
            # "failed only by profit / only by ROI / condition risk / …"
            # buckets without re-deriving them from free text.
            failure_reasons=list(failure_codes),
            risk_flags=list(op.risk_flags or []),
        ))
        _bump_failure_qstats(qstats, failure_codes)


def _new_qstats() -> dict:
    return {
        "raw_returned": 0,
        "negative_filtered": 0,
        "listings_fetched": 0,
        "new_listings": 0,
        "duplicates_skipped": 0,
        "listings_scored": 0,
        "exact_match_total": 0,
        "partial_match_total": 0,
        "broad_rejected_total": 0,
        "candidates_created": 0,
        "alerts_sent": 0,
        # v15.4 — per-query failure reason counters
        "failed_profit": 0,
        "failed_roi": 0,
        "failed_score": 0,
        "failed_confidence": 0,
        "failed_match_quality": 0,
        "failed_active_only": 0,
        "failed_battery_health": 0,
        "failed_risk_flags": 0,
        "failed_comp_pool": 0,
        "failed_no_comps": 0,
        "failed_other": 0,
    }


def _persist_query_performance(
    scan_run_id: int, terms: str, category: str, qstats: dict,
) -> None:
    from .models import QueryPerformanceRow
    with session_scope() as s:
        s.add(QueryPerformanceRow(
            scan_run_id=scan_run_id,
            query_terms=terms,
            category=category,
            engine_version=CURRENT_ENGINE_VERSION,
            **qstats,
        ))


def run_once() -> int:
    """Run one full pipeline pass. Returns alerts sent."""
    from .pricing.comps import (
        reset_stats, get_stats, reset_near_misses, get_near_misses,
    )

    sources = _build_sources()
    queries = get_queries()
    scan_id = _start_scan(sources, queries)
    counters = {"listings": 0, "reviewed": 0, "alerted": 0}
    reset_stats()
    reset_near_misses()

    log.info("running %d queries:", len(queries))
    for q in queries:
        log.info("  - %s [%s]", q.terms, q.category)

    for src in sources:
        log.info("scanning source: %s", src.name)
        if src.name == "mock":
            mock_qstats = _new_qstats()
            for listing in src.fetch(query="", category="", limit=100):
                _process_listing(listing, scan_id, counters, qstats=mock_qstats)
            _persist_query_performance(scan_id, "_mock_", "mock", mock_qstats)
        else:
            for q in queries:
                log.info("  query: '%s' [%s]", q.terms, q.category)
                qstats = _new_qstats()
                try:
                    for listing in src.fetch(
                        query=q.query_string,
                        category=q.category,
                        limit=50,
                    ):
                        qstats["raw_returned"] += 1
                        if q.title_matches_negatives(listing.title):
                            qstats["negative_filtered"] += 1
                            continue
                        _process_listing(listing, scan_id, counters, qstats=qstats)
                except Exception as e:
                    log.error("  %s failed on '%s': %s", src.name, q.terms, e)
                _persist_query_performance(scan_id, q.terms, q.category, qstats)

    _finish_scan(scan_id, counters)
    stats = get_stats()
    log.info("=" * 60)
    log.info("scan complete: listings=%d  candidates=%d  alerts=%d",
             counters["listings"], counters["reviewed"], counters["alerted"])
    log.info("comp engine: scanned=%d  scored=%d", stats.scanned, stats.scored)
    log.info("  exact matches: %d", stats.exact_match)
    log.info("  partial fallback: %d", stats.partial_match)
    log.info("  broad-only rejected: %d", stats.broad_rejected)
    log.info("  no comps found: %d", stats.no_comps)
    log.info("  weak match (insufficient): %d", stats.weak_match)
    log.info("=" * 60)

    # Near-miss report when no candidates fired
    if counters["reviewed"] == 0:
        misses = get_near_misses(limit=20)
        if misses:
            log.info("NEAR-MISS REPORT (top %d by score)", len(misses))
            log.info("-" * 60)
            for m in misses:
                log.info(
                    "  %s%.2f → %s%.2f  ROI=%.0f%%  score=%.2f  conf=%.2f  "
                    "match=%.2f",
                    settings.currency_symbol, m.price,
                    settings.currency_symbol, m.expected_resale,
                    m.roi * 100, m.score, m.confidence, m.match_quality,
                )
                log.info("    title: %s", m.title[:80])
                log.info("    why:   %s", m.fail_reason)
            log.info("=" * 60)

    return counters["alerted"]
