"""Shared test fixtures: an aiosqlite-backed API client seeded with the real fixtures.

No Docker/Postgres in the build env (DECISIONS D-002), so the API suite runs against SQLite via
aiosqlite. The app code is DB-agnostic; the same code targets Postgres in production.
"""

from __future__ import annotations

import glob
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models import Base
from scraper.parser import parse_filing
from scraper.store import upsert_filing


@pytest_asyncio.fixture
async def client(tmp_path) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # seed the eMarketStorage fixtures (their filing_id comes from the PDF cover page).
    # 1Info fixtures are excluded here — they have no cover page, so their id arrives via the
    # scraper's listing metadata, which this DB-seeding path does not carry. The API tests assert
    # counts derived from the 7 eMarketStorage filings.
    for f in sorted(glob.glob("tests/fixtures/*.pdf")):
        # only the eMarketStorage fixtures parse via the default PDF path; other countries'
        # fixtures (1Info, AMF-France, …) use their own adapters and are covered by their tests.
        if any(x in f for x in ("oneinfo_", "amf_fr", "afm_nl", "bafin_")):
            continue
        parsed = parse_filing(f, source_url=f"https://example/{f}")
        async with sessionmaker() as s:
            await upsert_filing(s, parsed)
            await s.commit()

    # late import so the app module is loaded once
    from api import deps
    from api.main import app

    async def _override_session() -> AsyncIterator:
        async with sessionmaker() as s:
            yield s

    async def _no_rate_limit() -> None:
        return None

    app.dependency_overrides[deps.db_session] = _override_session
    app.dependency_overrides[deps.rate_limit] = _no_rate_limit

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
