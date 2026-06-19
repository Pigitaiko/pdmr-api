"""Scraper tests — offline against the committed listing HTML fixture."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scraper.emarketstorage import parse_listing
from scraper.http import PoliteClient
from scraper.oneinfo import fetch_internal_dealing as oneinfo_fetch

LISTING = Path(__file__).parent / "fixtures" / "emarketstorage_listing.html"


def test_parse_listing_finds_internal_dealing():
    html = LISTING.read_text(encoding="utf-8")
    items = parse_listing(html)
    # the captured page contains CEMBRE and TINEXTA internal-dealing filings
    assert len(items) >= 2
    urls = {it.url for it in items}
    assert all(u.startswith("https://www.emarketstorage.it/sites/") for u in urls)
    assert len(urls) == len(items)  # deduped

    issuers = {it.issuer for it in items if it.issuer}
    assert any("CEMBRE" in i for i in issuers)

    for it in items:
        assert it.url.endswith(".pdf")
        assert it.source == "emarketstorage"
        if it.published_date:
            assert isinstance(it.published_date, date)


def test_parse_listing_excludes_non_internal_dealing():
    html = LISTING.read_text(encoding="utf-8")
    items = parse_listing(html)
    blob = " ".join((it.title or "") for it in items).lower()
    # only internal-dealing items survive the filter
    assert "internal" in blob or "allegato" in blob
    for it in items:
        assert it.title is None or (
            "internal" in it.title.lower() or "allegato" in it.title.lower()
        )


@pytest.mark.asyncio
async def test_oneinfo_is_stubbed():
    client = PoliteClient(respect_robots=False)
    with pytest.raises(NotImplementedError):
        await oneinfo_fetch(client)
    await client.aclose()
