"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router
from config import get_settings
from database import dispose_engine

log = structlog.get_logger()


# observable state for the /status diagnostic endpoint (tokenless; no sensitive data)
_BOOTSTRAP_STATE: dict[str, Any] = {
    "enabled": None,
    "started": False,
    "finished": False,
    "result": None,
    "error": None,
}


async def _bootstrap_scrape_if_empty() -> None:
    """One-shot background ingest on first boot when the DB has no filings. Lets the cloud
    deploy serve real data without a separate always-on scraper worker."""
    _BOOTSTRAP_STATE["started"] = True
    try:
        from sqlalchemy import func, select

        from database import session_scope
        from models import Filing

        async with session_scope() as session:
            count = (await session.execute(select(func.count()).select_from(Filing))).scalar_one()
        if count:
            _BOOTSTRAP_STATE["result"] = {"skipped": "db not empty", "count": count}
            return
        from scraper.ingest import run

        log.info("bootstrap_scrape_start")
        stats = await run(max_pages=get_settings().bootstrap_max_pages, source="all")
        _BOOTSTRAP_STATE["result"] = stats
        log.info("bootstrap_scrape_done", **stats)
    except Exception as exc:  # noqa: BLE001 - never let bootstrap crash the app
        _BOOTSTRAP_STATE["error"] = f"{type(exc).__name__}: {exc}"
        log.error("bootstrap_scrape_failed", error=str(exc))
    finally:
        _BOOTSTRAP_STATE["finished"] = True


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


# hold strong references to background tasks: asyncio keeps only a weak ref, so without this the
# GC can cancel the bootstrap scrape mid-flight (silently — no exception), leaving the DB empty.
_bg_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    if get_settings().bootstrap_scrape:
        task = asyncio.create_task(_bootstrap_scrape_if_empty())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    yield
    await dispose_engine()


app = FastAPI(
    title="PDMR Insider Transactions API",
    version="0.1.0",
    description=(
        "Machine-readable Italian PDMR (Art. 19 MAR) insider-dealing filings, parsed from the "
        "Allegato 3F form. Public regulatory data; not investment advice."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "pdmr-api", "version": "0.1.0"}


@app.get("/status")
async def status() -> dict[str, Any]:
    """Tokenless diagnostic: current filing count + what the first-boot bootstrap actually did."""
    from sqlalchemy import func, select

    from database import session_scope
    from models import Filing

    try:
        async with session_scope() as session:
            filings = (
                await session.execute(select(func.count()).select_from(Filing))
            ).scalar_one()
        db_ok = True
    except Exception as exc:  # noqa: BLE001 - report DB errors instead of crashing the probe
        filings, db_ok = None, f"{type(exc).__name__}: {exc}"
    return {
        "filings": filings,
        "db_ok": db_ok,
        "bootstrap_enabled": get_settings().bootstrap_scrape,
        "bootstrap": _BOOTSTRAP_STATE,
    }


_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
_landing = os.path.join(_static_dir, "landing.html")


@app.get("/", include_in_schema=False)
async def landing() -> FileResponse:
    """Marketing landing page (progressively enhanced with live API data)."""
    return FileResponse(_landing)


# minimal static dashboard at /dashboard (no build step)
if os.path.isdir(_static_dir):
    app.mount("/dashboard", StaticFiles(directory=_static_dir, html=True), name="dashboard")
