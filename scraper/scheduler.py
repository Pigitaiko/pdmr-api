"""APScheduler-based scheduler: poll the sources on a market-hours-aware cadence.

Cadence (Europe/Rome): every 15 min during CET market hours (~09:00–18:00 weekdays),
hourly otherwise. Each tick runs the idempotent ingestion pipeline, so overlapping schedules
are harmless.

Run: ``uv run python -m scraper.scheduler``
"""

from __future__ import annotations

import asyncio

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import dispose_engine
from scraper.ingest import run

log = structlog.get_logger()

TZ = "Europe/Rome"


async def _tick() -> None:
    try:
        stats = await run(max_pages=5)
        log.info("scheduler_tick_done", **stats)
    except Exception as exc:  # noqa: BLE001 - keep the scheduler alive across failures
        log.error("scheduler_tick_failed", error=str(exc))


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)
    # every 15 min during market hours, weekdays
    scheduler.add_job(
        _tick,
        CronTrigger(day_of_week="mon-fri", hour="9-17", minute="*/15", timezone=TZ),
        id="market_hours",
        max_instances=1,
        coalesce=True,
    )
    # hourly outside market hours, weekdays
    scheduler.add_job(
        _tick,
        CronTrigger(day_of_week="mon-fri", hour="0-8,18-23", minute=0, timezone=TZ),
        id="off_hours_weekday",
        max_instances=1,
        coalesce=True,
    )
    # hourly at weekends
    scheduler.add_job(
        _tick,
        CronTrigger(day_of_week="sat,sun", minute=0, timezone=TZ),
        id="weekend",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def main() -> None:
    scheduler = build_scheduler()
    scheduler.start()
    log.info("scheduler_started", timezone=TZ, jobs=[j.id for j in scheduler.get_jobs()])
    # run one tick immediately on startup so a fresh deploy backfills without waiting
    await _tick()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("scheduler_stopping")
    finally:
        scheduler.shutdown(wait=False)
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
