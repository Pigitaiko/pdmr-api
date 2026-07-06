"""run('all') must isolate per-source failures so one bad source can't abort the whole batch
(this is what the first-boot bootstrap scrape relies on)."""

from __future__ import annotations

import pytest

from scraper import ingest


@pytest.mark.anyio
async def test_run_all_isolates_source_failure(monkeypatch):
    # both sources are unknown -> each run(src) raises ValueError before touching the DB;
    # run('all') must swallow them and still return an aggregated stats dict without raising.
    monkeypatch.setattr(ingest, "_ALL_SOURCES", ("does_not_exist", "also_bogus"))
    stats = await ingest.run(max_pages=1, source="all")
    # totals stay zero, the run doesn't raise, and each bad source is reported in by_source
    assert stats["discovered"] == 0
    assert stats["ingested"] == 0
    assert stats["failed"] == 0
    assert set(stats["by_source"]) == {"does_not_exist", "also_bogus"}
    assert all("error" in v for v in stats["by_source"].values())
