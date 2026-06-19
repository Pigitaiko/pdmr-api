"""Polite async HTTP client: descriptive UA, ≥1 req/s throttle, 429 backoff, robots.txt.

Used by the scrapers to respect the sources (CLAUDE.md "Conventions").
"""

from __future__ import annotations

import asyncio
import time
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from config import get_settings

log = structlog.get_logger()


class PoliteClient:
    def __init__(self, base_url: str = "", respect_robots: bool = True) -> None:
        settings = get_settings()
        self._delay = settings.request_delay_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._respect_robots = respect_robots
        self._robots: RobotFileParser | None = None
        self._base_url = base_url
        self._ua = settings.user_agent

    async def _ensure_robots(self) -> None:
        if not self._respect_robots or self._robots is not None or not self._base_url:
            return
        rp = RobotFileParser()
        try:
            resp = await self._client.get("/robots.txt")
            rp.parse(resp.text.splitlines())
        except Exception:  # noqa: BLE001 - missing robots.txt => allow
            rp.parse([])
        self._robots = rp

    async def allowed(self, url: str) -> bool:
        await self._ensure_robots()
        if self._robots is None:
            return True
        return self._robots.can_fetch(self._ua, url)

    async def _throttle(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._delay:
                await asyncio.sleep(self._delay - elapsed)
            self._last_request = time.monotonic()

    async def get(self, url: str, *, max_retries: int = 3) -> httpx.Response:
        if self._respect_robots and not await self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        backoff = 2.0
        for attempt in range(max_retries + 1):
            await self._throttle()
            resp = await self._client.get(url)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", backoff))
                log.warning("rate_limited", url=url, wait=wait, attempt=attempt)
                await asyncio.sleep(wait)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp
        raise httpx.HTTPStatusError(
            "exceeded retries (429)",
            request=resp.request,
            response=resp,  # type: ignore[possibly-undefined]
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
