"""FastAPI dependencies: DB session and a Redis token-bucket rate limiter."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_session

log = structlog.get_logger()

# in-process fallback bucket, used when Redis is unavailable (e.g. local/test)
_local_hits: dict[str, list[float]] = {}


async def db_session() -> AsyncIterator[AsyncSession]:
    async for s in get_session():
        yield s


async def _redis_allow(key: str, limit: int, window: int) -> bool | None:
    """Return True/False using Redis, or None if Redis is unavailable."""
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(get_settings().redis_url)
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window)
        await client.aclose()
        return count <= limit
    except Exception:  # noqa: BLE001 - degrade gracefully without Redis
        return None


def _local_allow(key: str, limit: int, window: int) -> bool:
    now = time.monotonic()
    hits = [t for t in _local_hits.get(key, []) if now - t < window]
    hits.append(now)
    _local_hits[key] = hits
    return len(hits) <= limit


async def rate_limit(request: Request) -> None:
    settings = get_settings()
    limit, window = settings.rate_limit_per_minute, 60
    ip = request.client.host if request.client else "unknown"
    bucket = int(time.time() // window)
    key = f"rl:{ip}:{bucket}"

    allowed = await _redis_allow(key, limit, window)
    if allowed is None:
        allowed = _local_allow(key, limit, window)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limit exceeded")


SessionDep = Depends(db_session)
RateLimitDep = Depends(rate_limit)
