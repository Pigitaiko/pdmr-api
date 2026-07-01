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
    assert stats == {
        "discovered": 0,
        "ingested": 0,
        "duplicates": 0,
        "failed": 0,
        "partial": 0,
    }
