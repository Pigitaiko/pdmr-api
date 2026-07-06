"""Shared background-scrape launcher + observable state.

Both the first-boot bootstrap (api/main.py) and the manual refresh endpoint (api/routes.py) launch
scrapes through here, so the run happens off the request path and its outcome — including a
per-source breakdown — is visible at ``GET /status`` without logs or a token.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger()

# latest scrape state, surfaced by /status
SCRAPE_STATE: dict[str, Any] = {
    "trigger": None,  # "bootstrap" | "manual"
    "source": None,
    "running": False,
    "started": False,
    "finished": False,
    "result": None,  # merged stats incl. by_source breakdown
    "error": None,
}

_tasks: set[asyncio.Task] = set()


async def _run_and_record(source: str, max_pages: int, trigger: str) -> None:
    from scraper.ingest import run

    SCRAPE_STATE.update(
        trigger=trigger,
        source=source,
        running=True,
        started=True,
        finished=False,
        result=None,
        error=None,
    )
    try:
        log.info("scrape_start", source=source, trigger=trigger)
        stats = await run(max_pages=max_pages, source=source)
        SCRAPE_STATE["result"] = stats
        log.info("scrape_done", source=source, ingested=stats.get("ingested"))
    except Exception as exc:  # noqa: BLE001 - record, never crash the app
        SCRAPE_STATE["error"] = f"{type(exc).__name__}: {exc}"
        log.error("scrape_failed", source=source, error=str(exc))
    finally:
        SCRAPE_STATE["running"] = False
        SCRAPE_STATE["finished"] = True


def launch_scrape(source: str, max_pages: int, trigger: str) -> bool:
    """Start a scrape in the background (strong ref held so GC can't cancel it).

    Returns False if a scrape is already running (so we don't stack concurrent scrapes).
    """
    if SCRAPE_STATE["running"]:
        return False
    task = asyncio.create_task(_run_and_record(source, max_pages, trigger))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
