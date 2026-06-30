"""Config tests — DATABASE_URL normalization for managed cloud hosts."""

from __future__ import annotations

import pytest

from config import Settings


@pytest.mark.parametrize(
    "raw,expected",
    [
        # managed hosts hand out these — must become the async driver
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        ("postgresql://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        # already-correct URLs pass through untouched
        ("postgresql+asyncpg://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        # sqlite (tests) untouched
        ("sqlite+aiosqlite:///./x.db", "sqlite+aiosqlite:///./x.db"),
    ],
)
def test_database_url_normalized(raw, expected):
    assert Settings(database_url=raw).database_url == expected
