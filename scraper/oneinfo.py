"""1Info (Computershare) scraper — STUB.

`https://www.1info.it` redirects to `/PORTALE1INFO`, a Vue single-page application that renders
its content client-side (the server delivers ~265 chars of static HTML). The project forbids
Selenium/headless browsers, so a plain httpx+BeautifulSoup scrape cannot reach the filing list.

See DECISIONS.md D-007. To implement without a headless browser, the right next step is to
reverse-engineer the SPA's backing XHR/JSON endpoints (open the portal in a browser, watch the
network tab for the JSON API that feeds the document list) and call those directly with httpx —
which is allowed and far more robust than DOM scraping. Until then this source is unused;
eMarketStorage already provides the full internal-dealing flow.
"""

from __future__ import annotations

from scraper.emarketstorage import ListingItem
from scraper.http import PoliteClient

BASE = "https://www.1info.it"


async def fetch_internal_dealing(client: PoliteClient, *, max_pages: int = 5) -> list[ListingItem]:
    raise NotImplementedError(
        "1Info is a Vue SPA requiring JS; scraper stubbed (see DECISIONS.md D-007). "
        "TODO: reverse-engineer the portal's JSON/XHR API and fetch it with httpx."
    )
