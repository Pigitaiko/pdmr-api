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
from pathlib import Path

import structlog

from config import get_settings
from database import create_all, dispose_engine, session_scope
from scraper.emarketstorage import ListingItem, fetch_internal_dealing
from scraper.http import PoliteClient
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
        parsed = parse_filing(fh.name, source_url=item.url, source=item.source)
    if not parsed.filing_id:
        parsed.filing_id = _fallback_filing_id(item)

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


async def run(
    max_pages: int = 5, source: str = "emarketstorage", ensure_schema: bool = False
) -> dict[str, int]:
    if ensure_schema:
        await create_all()

    stats = {"discovered": 0, "ingested": 0, "duplicates": 0, "failed": 0, "partial": 0}
    seen = SeenStore()
    async with PoliteClient(base_url="https://www.emarketstorage.it") as client:
        items = await fetch_internal_dealing(client, max_pages=max_pages)
        stats["discovered"] = len(items)
        for item in items:
            if not await seen.is_new(item.url):
                stats["duplicates"] += 1
                continue
            try:
                result = await _ingest_one(client, item)
            except Exception as exc:  # noqa: BLE001 - never let one filing kill the batch
                log.error("ingest_failed", url=item.url, error=str(exc))
                stats["failed"] += 1
                continue
            if result == "duplicate":
                stats["duplicates"] += 1
            else:
                stats["ingested"] += 1
                if result in ("failed", "partial"):
                    stats[result] += 1
    await seen.aclose()
    log.info("ingest_summary", **stats)
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
