"""Switzerland — SIX Exchange Regulation (SER) management-transactions disclosure.

Swiss issuers disclose management transactions under Art. 56 of the SIX Listing Rules (the FMIA
regime, the Swiss analogue of MAR Art. 19). SER publishes them via a clean paginated JSON API
(discovered from the ``ser-ag.com`` React page's network calls; queried here over plain HTTP).

Swiss law is the key difference: notifications are published **without the person's name or date of
birth** — only the obligor's *function* (board vs. executive) and whether it's a related legal
entity. So every Swiss filing has issuer, role, buy/sell, price, volume (CHF), ISIN and date, but
``person_full_name`` is always ``None`` by regulation.
"""

from __future__ import annotations

import json
from datetime import date

from scraper.http import PoliteClient
from scraper.parser import ParsedFiling, ParsedTransaction, parse_decimal

API = "https://www.ser-ag.com/sheldon/management_transactions/v1/overview.json"

# SER obligorFunctionCode -> (role_raw, role_code)
_FUNCTION = {
    "1": ("Board of Directors", "DIR"),
    "2": ("Executive Management", "MGMT"),
}
_SECURITY = {"7": "Shares", "6": "Conversion/acquisition rights"}


def _to_date(yyyymmdd: int | None) -> date | None:
    if not yyyymmdd:
        return None
    s = str(yyyymmdd)
    if len(s) != 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def parse_item(item: dict) -> ParsedFiling:
    """Map one SER management-transaction record to a normalized ParsedFiling."""
    func = str(item.get("obligorFunctionCode") or "")
    role_raw, role_code = _FUNCTION.get(func, (f"Function {func}" if func else None, "OTHER"))
    bs = str(item.get("buySellIndicator") or "")
    tx_type = "A" if bs == "1" else "D" if bs == "2" else "O"
    txdate = _to_date(item.get("transactionDate"))
    tx = ParsedTransaction(
        seq=1,
        instrument_type=_SECURITY.get(str(item.get("securityTypeCode") or ""), "Security"),
        isin=item.get("ISIN") or None,
        nature_raw=("Acquisition" if tx_type == "A" else "Disposal" if tx_type == "D" else None),
        transaction_type=tx_type,
        price=parse_decimal(str(item.get("transactionAmountPerSecurityCHF") or "") or None),
        currency="CHF",
        volume=parse_decimal(str(item.get("transactionSize") or "") or None),
        transaction_date=txdate,
    )
    issuer = item.get("notificationSubmitter") or None
    # obligorRelatedPartyInd: 'L' = related legal entity, 'I' = individual, '' = unspecified
    is_legal = str(item.get("obligorRelatedPartyInd") or "") == "L"
    out = ParsedFiling(
        filing_id="ch-" + str(item.get("notificationId") or ""),
        source="six_ch",
        country="CH",
        source_url="https://www.ser-ag.com/en/resources/notifications-market-participants/management-transactions.html",
        title="Management transaction",
        market="SIX Swiss Exchange",
        issuer_name=issuer,
        person_full_name=None,  # Swiss law: names are not published
        is_legal_person=is_legal,
        position_status=role_raw,
        role_raw=role_raw,
        role_code=role_code,
        notification_type="initial",
        raw_text=json.dumps(item, ensure_ascii=False)[:4000],
        transactions=[tx],
    )
    ok = bool(issuer and tx.isin and txdate and tx.volume is not None)
    out.parse_status = "success" if ok else "partial"
    return out


def parse_overview(payload: dict) -> list[ParsedFiling]:
    return [parse_item(i) for i in payload.get("itemList", [])]


async def fetch_filings(client: PoliteClient, *, limit: int = 200) -> list[ParsedFiling]:
    """Fetch the most recent SER management transactions (newest first)."""
    resp = await client.get(f"{API}?pageSize={limit}&pageNumber=0&sortAttribute=byDate")
    return parse_overview(json.loads(resp.text))
