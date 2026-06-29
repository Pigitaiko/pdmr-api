"""1Info (Computershare) scraper — real implementation via the portal's JSON API.

`https://www.1info.it/PORTALE1INFO` is a RequireJS/Knockout SPA, but its data comes from a plain
JSON API that we call directly with httpx (no headless browser — see DECISIONS.md D-007):

- ``POST /PORTALE1INFO/API/Comunicati`` — DataTables server-side endpoint returning every stored
  comunicato as JSON (fields: ``mittente`` issuer, ``oggetto`` title, ``categoria``, ``pdf`` id,
  ``data``/``dataStoccaggio`` unix timestamps, ``filetype``).
- PDFs are fetched from ``/PdfViewer/PdfShow.aspx`` with the year parsed from the ``pdf`` id
  (``{ndg}_{seq}_{year}_oneinfo``).

We keep only internal-dealing filings (title contains "internal dealing" / "allegato 3f"). Because
1Info PDFs carry no eMarketStorage-style "Comunicato n." id, the listing supplies ``filing_id``
(the ``pdf`` id), issuer and publication date to the parser via ``ListingItem.meta``.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from scraper.emarketstorage import ListingItem
from scraper.http import PoliteClient

BASE = "https://www.1info.it"
API_COMUNICATI = "/PORTALE1INFO/API/Comunicati"
PDF_SHOW = "https://www.1info.it/PdfViewer/PdfShow.aspx"

_INTERNAL_DEALING_RE = re.compile(r"internal\s*dealing|allegato\s*3f", re.IGNORECASE)

# DataTables columns the server-side binder expects (omitting them NREs the API).
_COLUMNS = ["mittente", "categoria", "oggetto", "dataDiffusione", "dataStoccaggio", "allegato"]


def _datatables_payload(start: int, length: int, search: str = "") -> dict[str, str]:
    body: dict[str, str] = {
        "draw": "1",
        "start": str(start),
        "length": str(length),
        "search[value]": search,
        "search[regex]": "false",
        "order[0][column]": "4",  # dataStoccaggio
        "order[0][dir]": "desc",
    }
    for i, col in enumerate(_COLUMNS):
        body[f"columns[{i}][data]"] = col
        body[f"columns[{i}][name]"] = col
        body[f"columns[{i}][searchable]"] = "true"
        body[f"columns[{i}][orderable]"] = "true"
        body[f"columns[{i}][search][value]"] = ""
        body[f"columns[{i}][search][regex]"] = "false"
    return body


def pdf_url(pdf_id: str, filetype: str = "comunicati") -> str:
    """Build the direct PDF download URL for a 1Info ``pdf`` id (year is its 3rd segment)."""
    parts = pdf_id.split("_")
    year = parts[2] if len(parts) >= 3 and parts[2].isdigit() else str(datetime.now(UTC).year)
    return (
        f"{PDF_SHOW}?username=oneinfo&password=oneinfo&service="
        f"&type={filetype}&year={year}&file={pdf_id}.pdf"
    )


def _to_item(row: dict) -> ListingItem:
    pdf_id = row.get("pdf") or ""
    ts = row.get("data") or row.get("dataStoccaggio")
    published: date | None = None
    published_dt: datetime | None = None
    if isinstance(ts, int):
        published_dt = datetime.fromtimestamp(ts, tz=UTC)
        published = published_dt.date()
    issuer = row.get("mittente")
    title = row.get("oggetto")
    filing_id = f"{pdf_id}" if pdf_id else None
    return ListingItem(
        source="oneinfo",
        url=pdf_url(pdf_id, row.get("filetype") or "comunicati"),
        issuer=issuer,
        title=title,
        published_date=published,
        filing_id=filing_id,
        meta={
            "filing_id": filing_id,
            "issuer_name": issuer,
            "title": title,
            "published_at": published_dt,
            "market": str(row.get("idMercato")) if row.get("idMercato") else None,
        },
    )


def parse_listing(payload: dict) -> list[ListingItem]:
    """Filter a /API/Comunicati JSON response to internal-dealing filings."""
    items: list[ListingItem] = []
    for row in payload.get("data", []):
        title = row.get("oggetto") or ""
        if _INTERNAL_DEALING_RE.search(title):
            items.append(_to_item(row))
    return items


async def fetch_internal_dealing(
    client: PoliteClient, *, max_pages: int = 5, page_size: int = 200
) -> list[ListingItem]:
    """Page through /API/Comunicati and collect internal-dealing filings (dedup by URL)."""
    headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    seen: set[str] = set()
    out: list[ListingItem] = []
    for page in range(max_pages):
        resp = await client.post(
            API_COMUNICATI, data=_datatables_payload(page * page_size, page_size), headers=headers
        )
        payload = resp.json()
        rows = payload.get("data", [])
        for item in parse_listing(payload):
            if item.url not in seen:
                seen.add(item.url)
                out.append(item)
        if len(rows) < page_size:
            break  # last page
    return out
