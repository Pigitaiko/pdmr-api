"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pdmr:pdmr@localhost:5432/pdmr"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"
    user_agent: str = "PDMR-API-bot/0.1 (+contact@pdmr-api.example)"

    # politeness / scraping
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 30.0

    # api rate limiting (per-IP token bucket)
    rate_limit_per_minute: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()
