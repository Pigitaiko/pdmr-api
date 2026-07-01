"""Belgium — FSMA register of managers' transactions (MAR Art. 19).

Server-rendered (Drupal), no headless needed. ``/en/transaction-search`` lists per-notification
detail pages (``/en/manager-transaction/<slug>``); each detail page carries every field in labelled
Drupal markup: issuer, notifying person, role, instrument + ISIN, nature, place, date, currency,
quantity, price. We read the listing, then parse each detail page.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from scraper.http import PoliteClient
from scraper.parser import (
    ParsedFiling,
    ParsedTransaction,
    map_role_code,
    map_transaction_type,
    parse_decimal,
)

BASE = "https://www.fsma.be"
LISTING_PATH = "/en/transaction-search"
DETAIL_RE = re.compile(r"/en/manager-transaction/[a-z0-9-]+")
BRUSSELS = ZoneInfo("Europe/Brussels")

_LEGAL = re.compile(r"\b(NV|SA|BV|SRL|SPRL|S\.?A\.?|N\.?V\.?|LTD|GMBH|HOLDING)\b", re.I)


def _fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    for div in soup.select("div.field--label-inline, div.field"):
        lbl = div.select_one(".field__label, .field-label")
        val = div.select_one(".field__item, .field-item, .field__items")
        if lbl and val:
            out[lbl.get_text(" ", strip=True).rstrip(":")] = val.get_text(" ", strip=True)
    return out


def _date(raw: str | None) -> date | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_detail(html: str, slug: str) -> ParsedFiling:
    f = _fields(html)
    person = f.get("Notifying person")
    txdate = _date(f.get("Transaction Date"))
    pubdate = _date(f.get("Date of publication"))
    nature = f.get("Transaction Type")
    tx = ParsedTransaction(
        seq=1,
        instrument_type=f.get("Instrument Type"),
        isin=f.get("Instrument ISIN Code"),
        nature_raw=nature,
        transaction_type=map_transaction_type(nature),
        price=parse_decimal(f.get("Transaction Price")),
        currency=(f.get("Transaction Currency") or "EUR").upper()[:8],
        volume=parse_decimal(f.get("Transaction Quantity")),
        transaction_date=txdate,
        venue=f.get("Transaction Place"),
    )
    out = ParsedFiling(
        filing_id="be-" + slug,
        source="fsma_be",
        country="BE",
        source_url=BASE + "/en/manager-transaction/" + slug,
        title=nature or "Managers' transaction",
        market="Euronext Brussels",
        issuer_name=f.get("Issuer"),
        person_full_name=person,
        is_legal_person=bool(person and _LEGAL.search(person)),
        position_status=f.get("Declarer Type"),
        role_raw=f.get("Declarer Type"),
        role_code=map_role_code(f.get("Declarer Type")),
        notification_type="initial",
        published_at=(
            datetime.combine(pubdate, datetime.min.time(), BRUSSELS).astimezone(UTC)
            if pubdate
            else None
        ),
        raw_text=" | ".join(f"{k}={v}" for k, v in f.items())[:4000],
        transactions=[tx],
    )
    ok = bool(out.issuer_name and person and txdate and tx.volume is not None)
    out.parse_status = "success" if ok else "partial"
    return out


async def fetch_filings(client: PoliteClient, *, limit: int = 40) -> list[ParsedFiling]:
    """Read the FSMA listing, then parse detail pages for the most recent ``limit`` filings."""
    resp = await client.get(LISTING_PATH)
    slugs: list[str] = []
    seen: set[str] = set()
    for m in DETAIL_RE.finditer(resp.text):
        slug = m.group(0).rsplit("/", 1)[-1]
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    out: list[ParsedFiling] = []
    for slug in slugs[:limit]:
        try:
            detail = await client.get("/en/manager-transaction/" + slug)
            out.append(parse_detail(detail.text, slug))
        except Exception:  # noqa: BLE001 - skip a bad detail page, keep the batch
            continue
    return out
