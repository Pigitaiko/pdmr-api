"""United Kingdom — FCA National Storage Mechanism (NSM), UK MAR Art. 19 PDMR notifications.

Post-Brexit the UK retained MAR; managers'-transaction ("Director/PDMR Shareholding") notifications
are filed as RNS announcements and stored in the FCA's NSM. The NSM is a React app backed by an
Elasticsearch API (``api.data.fca.org.uk/search?index=fca-nsm-searchdata``, discovered from the
page's network calls). We query it for the PDMR announcement type, then download each RNS document —
the standard EU-harmonised Art. 19 template — and parse it with the shared harmonised-template
parser (the same Annex used across the other markets).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from scraper.http import PoliteClient
from scraper.nasdaq_nordic import parse_mar_text
from scraper.parser import (
    ParsedFiling,
    ParsedTransaction,
    map_role_code,
    map_transaction_type,
)

SEARCH_API = "https://api.data.fca.org.uk/search?index=fca-nsm-searchdata"
ARTEFACT = "https://data.fca.org.uk/artefacts/"
PDMR_TYPE = "Director/PDMR Shareholding"
_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://data.fca.org.uk",
    "Referer": "https://data.fca.org.uk/",
}


def _doc_text(html: str) -> str:
    """Strip an RNS HTML doc to text while preserving block boundaries as newlines, so the
    harmonised-template parser's line-anchored field extraction works (label on one line, value
    on the next)."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"(?i)<(br|/td|/tr|/p|/div|/li|/h[1-6]|/th)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&amp;", "&")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _search_body(limit: int) -> dict:
    return {
        "from": 0,
        "size": limit,
        "keyword": "PDMR Transaction",
        "sort": "submitted_date",
        "sortorder": "desc",
        "criteriaObj": {"criteria": [], "dateCriteria": []},
    }


def pdmr_hits(payload: dict) -> list[dict]:
    """Extract the PDMR-typed search hits' _source dicts."""
    hits = payload.get("hits", {}).get("hits", [])
    return [h["_source"] for h in hits if h.get("_source", {}).get("type") == PDMR_TYPE]


def build_filing(rec: dict, doc_html: str | None) -> ParsedFiling:
    """Assemble a filing from an NSM search record + its RNS document (harmonised template)."""
    dl = rec.get("download_link") or ""
    filing_id = "gb-" + (rec.get("disclosure_id") or dl.rsplit("/", 1)[-1].replace(".html", ""))
    published = None
    sd = rec.get("submitted_date")
    if sd:
        try:
            published = datetime.fromisoformat(sd.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            published = None

    fields = parse_mar_text(_doc_text(doc_html)) if doc_html else {}
    nature = fields.get("nature")
    # UK RNS prices are typically GBX (pence) or GBP; keep the extracted code, default GBP
    currency = (fields.get("currency") or "GBP")[:8]
    tx = ParsedTransaction(
        seq=1,
        instrument_type=fields.get("instrument"),
        isin=fields.get("isin"),
        nature_raw=nature,
        transaction_type=map_transaction_type(nature),
        price=fields.get("price"),
        currency=currency,
        volume=fields.get("volume"),
        transaction_date=fields.get("txdate"),
        venue=fields.get("venue"),
        venue_mic=fields.get("venue_mic"),
    )
    position = fields.get("position")
    out = ParsedFiling(
        filing_id=filing_id,
        source="fca_uk",
        country="GB",
        source_url=ARTEFACT + dl if dl else "https://data.fca.org.uk/",
        title=rec.get("headline") or "PDMR Transaction",
        market="London Stock Exchange",
        issuer_name=fields.get("issuer"),
        issuer_lei=fields.get("lei") or rec.get("lei"),
        person_full_name=fields.get("person"),
        position_status=position,
        role_raw=position,
        role_code=map_role_code(position),
        notification_type="initial",
        published_at=published,
        raw_text=(rec.get("headline") or "")[:4000],
        transactions=[tx],
    )
    ok = bool(
        out.issuer_name and out.person_full_name and fields.get("isin") and fields.get("txdate")
    )
    out.parse_status = "success" if ok else "partial"
    return out


async def fetch_filings(client: PoliteClient, *, limit: int = 40) -> list[ParsedFiling]:
    """Query the NSM for recent PDMR notifications and parse each RNS document."""
    resp = await client.post(SEARCH_API, json=_search_body(limit), headers=_HEADERS)
    records = pdmr_hits(json.loads(resp.text))
    out: list[ParsedFiling] = []
    for rec in records:
        dl = rec.get("download_link")
        doc = None
        if dl:
            try:
                doc = (await client.get(ARTEFACT + dl)).text
            except Exception:  # noqa: BLE001 - keep the batch; emit a listing-only partial
                doc = None
        out.append(build_filing(rec, doc))
    return out
