"""Background scheduler with double-start prevention."""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from .pipeline import run_once

log = logging.getLogger("scheduler")

_scheduler: BackgroundScheduler | None = None


def start_scheduler(interval_minutes: int = 10) -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("scheduler already running — skipping double start")
        return _scheduler

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        run_once, "interval",
        minutes=interval_minutes, id="scan",
        next_run_time=datetime.now(),
    )
    _scheduler.start()
    log.info("scheduler started — interval=%s min", interval_minutes)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        log.info("scheduler stopped")
    _scheduler = None
