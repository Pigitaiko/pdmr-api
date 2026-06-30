"""FastAPI application entry point."""

from __future__ import annotations

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
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


_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
_landing = os.path.join(_static_dir, "landing.html")


@app.get("/", include_in_schema=False)
async def landing() -> FileResponse:
    """Marketing landing page (progressively enhanced with live API data)."""
    return FileResponse(_landing)


# minimal static dashboard at /dashboard (no build step)
if os.path.isdir(_static_dir):
    app.mount("/dashboard", StaticFiles(directory=_static_dir, html=True), name="dashboard")
