"""Seed the database with the canonical CEMBRE fixture so the API has data pre-scraper.

Usage: ``uv run python -m seed``  (idempotent — safe to re-run).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from database import dispose_engine, session_scope
from scraper.parser import parse_filing
from scraper.store import upsert_filing

log = structlog.get_logger()

FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "cembre_0088-10-2026.pdf"
FIXTURE_URL = (
    "https://www.emarketstorage.it/sites/default/files/comunicati/2026-06/20260619_185962.pdf"
)


async def main() -> None:
    parsed = parse_filing(FIXTURE, source_url=FIXTURE_URL, source="emarketstorage")
    async with session_scope() as session:
        filing, created = await upsert_filing(session, parsed)
    log.info(
        "seed",
        filing_id=parsed.filing_id,
        created=created,
        transactions=len(parsed.transactions),
        status=parsed.parse_status,
    )
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
