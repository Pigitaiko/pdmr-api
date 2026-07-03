"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pdmr:pdmr@localhost:5432/pdmr"

    @field_validator("database_url")
    @classmethod
    def _async_pg_scheme(cls, v: str) -> str:
        """Managed hosts (Render, Heroku, Fly) hand out `postgres://` / `postgresql://`
        URLs; the async engine needs the explicit `+asyncpg` driver. Normalize so the same
        code runs locally and in the cloud without per-host config."""
        for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
            if v.startswith(prefix):
                return v
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://") :]
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://") :]
        return v

    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    user_agent: str = "PDMR-API-bot/0.1 (+https://github.com/Pigitaiko/pdmr-api)"

    # politeness / scraping
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 30.0

    # api rate limiting (per-IP token bucket)
    rate_limit_per_minute: int = 120

    # on first boot with an empty DB, run one scrape in the background (used by the cloud
    # deploy so the site has real data without a separate always-on worker).
    bootstrap_scrape: bool = False
    bootstrap_max_pages: int = 6

    # token that gates the manual refresh endpoint (GET /v1/admin/refresh?token=...).
    # empty = endpoint disabled. Lets an operator trigger/observe a scrape on demand.
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
