"""Ingestion pipeline: discover filings -> download PDF -> parse -> upsert (idempotent).

Run: ``uv run python -m scraper.ingest [--max-pages N] [--source emarketstorage]``
Backfill: ``uv run python -m scraper.ingest --backfill --year 2026 --max-pages 100``

Idempotency has two layers: a Redis SET of seen PDF URLs (skips re-download) and the DB unique
``filings.filing_id`` (skips re-insert). Redis is optional — without it, dedup still holds at the
DB layer (CLAUDE.md, DECISIONS D-005).
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog

from config import get_settings
from database import create_all, dispose_engine, session_scope
from scraper.afm_nl import fetch_filings as nl_fetch
from scraper.amf_fr import fetch_filings as france_fetch
from scraper.emarketstorage import ListingItem, fetch_internal_dealing
from scraper.fi_sweden import fetch_filings as sweden_fetch
from scraper.fsma_be import fetch_filings as belgium_fetch
from scraper.http import PoliteClient
from scraper.nasdaq_nordic import fetch_filings as nasdaq_fetch
from scraper.oneinfo import fetch_internal_dealing as oneinfo_fetch
from scraper.oslo_bors_no import fetch_filings as norway_fetch
from scraper.parser import parse_filing
from scraper.store import upsert_filing

log = structlog.get_logger()


class SeenStore:
    """Redis-backed set of already-seen URLs, with a no-op fallback when Redis is down."""

    def __init__(self) -> None:
        self._client = None
        self._key = "pdmr:seen:urls"

    async def _redis(self):
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(get_settings().redis_url)
            await client.ping()
            self._client = client
        except Exception:  # noqa: BLE001 - operate without Redis
            self._client = None
        return self._client

    async def is_new(self, url: str) -> bool:
        client = await self._redis()
        if client is None:
            return True  # no cache; rely on DB-level dedup
        return bool(await client.sadd(self._key, url))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def _fallback_filing_id(item: ListingItem) -> str:
    """Derive a stable id from the PDF filename when parsing can't find 'Comunicato n.'."""
    return "url:" + Path(item.url).stem


async def _ingest_one(client: PoliteClient, item: ListingItem) -> str:
    resp = await client.get(item.url)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as fh:
        fh.write(resp.content)
        fh.flush()
        parsed = parse_filing(fh.name, source_url=item.url, source=item.source, meta=item.meta)
    if not parsed.filing_id:
        parsed.filing_id = item.filing_id or _fallback_filing_id(item)

    async with session_scope() as session:
        _, created = await upsert_filing(session, parsed)

    log.info(
        "ingest",
        filing_id=parsed.filing_id,
        issuer=parsed.issuer_name or item.issuer,
        status=parsed.parse_status,
        transactions=len(parsed.transactions),
        created=created,
        url=item.url,
    )
    return parsed.parse_status if created else "duplicate"


async def _persist(parsed) -> str:  # noqa: ANN001 - ParsedFiling
    """Upsert an already-parsed filing (used by structured sources). Returns status."""
    async with session_scope() as session:
        _, created = await upsert_filing(session, parsed)
    log.info(
        "ingest",
        filing_id=parsed.filing_id,
        issuer=parsed.issuer_name,
        country=parsed.country,
        status=parsed.parse_status,
        transactions=len(parsed.transactions),
        created=created,
    )
    return parsed.parse_status if created else "duplicate"


def _tally(stats: dict[str, int], result: str) -> None:
    if result == "duplicate":
        stats["duplicates"] += 1
    else:
        stats["ingested"] += 1
        if result in ("failed", "partial"):
            stats[result] += 1


# PDF/filing sources: discover ListingItems -> download -> parse -> upsert
_Fetcher = Callable[..., Awaitable[list[ListingItem]]]
_SOURCES: dict[str, tuple[str, _Fetcher]] = {
    "emarketstorage": ("https://www.emarketstorage.it", fetch_internal_dealing),
    "oneinfo": ("https://www.1info.it", oneinfo_fetch),
}

# structured sources: fetch already-parsed ParsedFiling objects (e.g. a regulator CSV/API/HTML)
_STRUCTURED: dict[str, tuple[str, Callable[..., Awaitable[list]]]] = {
    "fi_sweden": ("https://marknadssok.fi.se", sweden_fetch),
    "afm_nl": ("https://www.afm.nl", nl_fetch),
    "amf_fr": ("https://bdif.amf-france.org", france_fetch),
    "fsma_be": ("https://www.fsma.be", belgium_fetch),
    "nasdaq_nordic": ("https://api.news.eu.nasdaq.com", nasdaq_fetch),
    "oslo_bors_no": ("https://api3.oslo.oslobors.no", norway_fetch),
}

# Structured sources first (fast, reliable JSON/CSV/HTML — Sweden alone is ~900 rows in one CSV),
# then the PDF-download sources (Italy) last. This means a slow or interrupted scrape still yields
# broad multi-country data quickly instead of only Italy.
_ALL_SOURCES = (*_STRUCTURED, *_SOURCES)

# max wall-clock per source in a `run("all")` batch, so one slow/blocked source (e.g. a host that
# throttles datacenter IPs) can't stall the whole scrape. Generous enough for the PDF-download
# sources over a ≤1 req/s throttle.
_SOURCE_TIMEOUT_S = 240


async def run(
    max_pages: int = 5, source: str = "emarketstorage", ensure_schema: bool = False
) -> dict[str, int]:
    if ensure_schema:
        await create_all()
    if source == "all":
        merged: dict = {"discovered": 0, "ingested": 0, "duplicates": 0, "failed": 0, "partial": 0}
        by_source: dict[str, dict] = {}
        for src in _ALL_SOURCES:
            # isolate + time-bound each source: a slow/blocked source (e.g. a host that rejects
            # datacenter IPs) must neither abort the batch nor stall it indefinitely.
            try:
                r = await asyncio.wait_for(run(max_pages, src), timeout=_SOURCE_TIMEOUT_S)
                for k, v in r.items():
                    merged[k] += v
                by_source[src] = {k: r[k] for k in ("discovered", "ingested", "failed")}
            except TimeoutError:
                log.error("source_timeout", source=src, timeout=_SOURCE_TIMEOUT_S)
                by_source[src] = {"error": f"timeout>{_SOURCE_TIMEOUT_S}s"}
            except Exception as exc:  # noqa: BLE001 - one source never kills the whole run
                log.error("source_failed", source=src, error=str(exc))
                by_source[src] = {"error": f"{type(exc).__name__}: {exc}"}
        merged["by_source"] = by_source
        return merged

    stats = {"discovered": 0, "ingested": 0, "duplicates": 0, "failed": 0, "partial": 0}
    seen = SeenStore()

    if source in _STRUCTURED:
        base_url, fetcher = _STRUCTURED[source]
        async with PoliteClient(base_url=base_url) as client:
            filings = await fetcher(client)
            stats["discovered"] = len(filings)
            for parsed in filings:
                if not parsed.filing_id or not await seen.is_new(parsed.filing_id):
                    stats["duplicates"] += 1
                    continue
                try:
                    _tally(stats, await _persist(parsed))
                except Exception as exc:  # noqa: BLE001 - one row never kills the batch
                    log.error("ingest_failed", filing_id=parsed.filing_id, error=str(exc))
                    stats["failed"] += 1
        await seen.aclose()
        log.info("ingest_summary", source=source, **stats)
        return stats

    if source not in _SOURCES:
        raise ValueError(f"unknown source: {source}")
    base_url, fetcher = _SOURCES[source]
    async with PoliteClient(base_url=base_url) as client:
        items = await fetcher(client, max_pages=max_pages)
        stats["discovered"] = len(items)
        for item in items:
            if not await seen.is_new(item.url):
                stats["duplicates"] += 1
                continue
            try:
                _tally(stats, await _ingest_one(client, item))
            except Exception as exc:  # noqa: BLE001 - never let one filing kill the batch
                log.error("ingest_failed", url=item.url, error=str(exc))
                stats["failed"] += 1
    await seen.aclose()
    log.info("ingest_summary", source=source, **stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="PDMR ingestion pipeline")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--source", default="emarketstorage")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--year", type=int)
    parser.add_argument(
        "--ensure-schema",
        action="store_true",
        help="create tables before ingest (dev/sqlite; prod uses alembic)",
    )
    args = parser.parse_args()
    pages = args.max_pages
    if args.backfill:
        pages = max(pages, 100)
        log.info("backfill_mode", year=args.year, max_pages=pages)

    asyncio.run(_main(pages, args.source, args.ensure_schema))


async def _main(pages: int, source: str, ensure_schema: bool) -> None:
    try:
        await run(max_pages=pages, source=source, ensure_schema=ensure_schema)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    main()
