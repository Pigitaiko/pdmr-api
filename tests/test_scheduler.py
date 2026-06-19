"""Scheduler wiring test (does not start the loop or hit the network)."""

from __future__ import annotations

from scraper.scheduler import build_scheduler


def test_build_scheduler_registers_three_jobs():
    scheduler = build_scheduler()
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert job_ids == {"market_hours", "off_hours_weekday", "weekend"}
    for job in scheduler.get_jobs():
        # Europe/Rome timezone on every trigger
        assert "Europe/Rome" in str(job.trigger.timezone)
