"""Alert dispatcher — console + Telegram + Discord, with DB linkage."""
from __future__ import annotations
import logging
from typing import Optional
import httpx
from .config import settings
from .models import Opportunity, AlertLogRow, _utcnow
from .db import session_scope

log = logging.getLogger("alerts")


def _format(op: Opportunity) -> str:
    l = op.listing
    flags = ", ".join(op.risk_flags) or "none"
    cur = settings.currency_symbol
    return (
        f"{'='*60}\n"
        f"  ALERT: {l.title}\n"
        f"  source: {l.source}  |  {l.source_url}\n"
        f"  price: {cur}{l.price:.2f} (+{cur}{l.shipping:.2f} ship)\n"
        f"  est. resale: {cur}{op.expected_resale:.2f}\n"
        f"  net profit: {cur}{op.net_profit:.2f}  ROI: {op.roi*100:.1f}%\n"
        f"  score: {op.score:.2f}  conf: {op.confidence:.2f}  "
        f"match: {op.match_quality:.2f}\n"
        f"  comps: {op.comp_source} ({op.comp_count} samples)\n"
        f"  match detail: {op.match_details}\n"
        f"  flags: {flags}\n"
        f"{'='*60}"
    )


def _log_alert(
    channel: str,
    success: bool,
    error: Optional[str] = None,
    *,
    opportunity_id: Optional[int] = None,
    review_candidate_id: Optional[int] = None,
) -> None:
    try:
        with session_scope() as s:
            s.add(AlertLogRow(
                channel=channel,
                opportunity_id=opportunity_id,
                review_candidate_id=review_candidate_id,
                sent_at=_utcnow(),
                success=success,
                error=error,
            ))
    except Exception:
        # Never let logging failures break the pipeline
        pass


def send_alert(
    op: Opportunity,
    *,
    opportunity_id: Optional[int] = None,
    review_candidate_id: Optional[int] = None,
) -> None:
    msg = _format(op)
    print(f"\n{msg}\n", flush=True)
    _log_alert("console", True,
               opportunity_id=opportunity_id,
               review_candidate_id=review_candidate_id)

    if settings.telegram_bot_token and settings.telegram_chat_id:
        try:
            httpx.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": msg},
                timeout=10,
            )
            _log_alert("telegram", True,
                       opportunity_id=opportunity_id,
                       review_candidate_id=review_candidate_id)
        except Exception as e:
            log.error("telegram failed: %s", e)
            _log_alert("telegram", False, str(e),
                       opportunity_id=opportunity_id,
                       review_candidate_id=review_candidate_id)

    if settings.discord_webhook_url:
        try:
            httpx.post(
                settings.discord_webhook_url,
                json={"content": f"```\n{msg}\n```"},
                timeout=10,
            )
            _log_alert("discord", True,
                       opportunity_id=opportunity_id,
                       review_candidate_id=review_candidate_id)
        except Exception as e:
            log.error("discord failed: %s", e)
            _log_alert("discord", False, str(e),
                       opportunity_id=opportunity_id,
                       review_candidate_id=review_candidate_id)
