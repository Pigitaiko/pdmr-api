"""eMarketStorage (Teleborsa) scraper — discovers internal-dealing (Allegato 3F) filings.

Server-rendered Drupal listing paginated with ``?page=N``. We parse the press-releases listing
(node/21) and optionally the regulated-documents listing (node/30), and keep only items whose
title/context marks them as internal dealing. Plain httpx + BeautifulSoup (no JS).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup

from scraper.http import PoliteClient

BASE = "https://www.emarketstorage.it"
PRESS_RELEASES_PATH = "/en/node/21"
DOCUMENTS_PATH = "/en/node/30"

_PDF_RE = re.compile(r"/sites/default/files/comunicati/.*\.pdf$")
_INTERNAL_DEALING_RE = re.compile(r"internal\s*dealing|allegato\s*3f", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2}):(\d{2})")


@dataclass(frozen=True)
class ListingItem:
    source: str
    url: str  # absolute PDF URL
    issuer: str | None
    title: str | None
    published_date: date | None
    filing_id: str | None = None  # known up-front for sources that expose an id (e.g. 1Info)
    meta: dict | None = None  # listing hints passed to the parser (filing_id, issuer, published_at)


def _row_text(anchor) -> str:
    node = anchor
    for _ in range(4):
        if node.parent is not None:
            node = node.parent
    return node.get_text(" ", strip=True)


def parse_listing(html: str, *, source: str = "emarketstorage") -> list[ListingItem]:
    """Extract internal-dealing filings from one listing page."""
    soup = BeautifulSoup(html, "lxml")
    items: dict[str, ListingItem] = {}
    for a in soup.find_all("a", href=_PDF_RE):
        href = str(a.get("href") or "")
        if not href or href in items:
            continue
        ctx = _row_text(a)
        if not _INTERNAL_DEALING_RE.search(ctx):
            continue

        published: date | None = None
        m = _DATE_RE.search(ctx)
        issuer = None
        title = None
        if m:
            mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            published = date(yyyy, mm, dd)
            # text after the timestamp: "ISSUER  TITLE"
            rest = ctx[m.end() :].strip()
            # title = the internal-dealing phrase onward; issuer = preceding tokens
            dm = _INTERNAL_DEALING_RE.search(rest)
            if dm:
                issuer = rest[: dm.start()].strip(" -–") or None
                title = rest[dm.start() :].strip() or None
        url = href if href.startswith("http") else BASE + href
        items[href] = ListingItem(
            source=source, url=url, issuer=issuer, title=title, published_date=published
        )
    return list(items.values())


async def fetch_internal_dealing(
    client: PoliteClient,
    *,
    max_pages: int = 5,
    path: str = PRESS_RELEASES_PATH,
) -> list[ListingItem]:
    """Paginate a listing and collect internal-dealing items (dedup by URL across pages)."""
    seen: set[str] = set()
    out: list[ListingItem] = []
    for page in range(max_pages):
        url = f"{path}?page={page}" if page else path
        resp = await client.get(url)
        page_items = parse_listing(resp.text)
        new = [it for it in page_items if it.url not in seen]
        if not new and page > 0:
            # no new internal-dealing items on this page; keep going a bit, then stop
            if page_items:
                continue
            break
        for it in new:
            seen.add(it.url)
            out.append(it)
    return out
