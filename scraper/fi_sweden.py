"""Sweden — Finansinspektionen (FI) PDMR-transactions register.

Unlike Italy (PDF filings), FI publishes a fully structured, public CSV export of every Art. 19
MAR insider transaction since 2016-07-03 — including the PDMR name, role, ISIN, price (in SEK)
and venue. We fetch the CSV from the public search client and map each row straight to a
``ParsedFiling`` — no PDF parsing. (Source for the export endpoint: FI's public "marknadssök"
client, as used by the open insynsregistret libraries.)
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from scraper.http import PoliteClient
from scraper.parser import (
    ParsedFiling,
    ParsedTransaction,
    map_role_code,
    map_transaction_type,
    parse_decimal,
)

BASE = "https://marknadssok.fi.se"
EXPORT_PATH = "/publiceringsklient/en-GB/Search/Search"
STOCKHOLM = ZoneInfo("Europe/Stockholm")

_LEGAL_HINT = (
    " AB",
    " AS",
    " ASA",
    " S.A.",
    " SPA",
    " LTD",
    " OY",
    " A/S",
    "AKTIEBOLAG",
    "HOLDING",
)


def _g(row: dict, key: str) -> str:
    return (row.get(key) or "").replace("\xa0", " ").strip()


def _dt(raw: str) -> datetime | None:
    """FI timestamps look like '30/06/2026 15:54:53' (Europe/Stockholm)."""
    raw = raw.strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=STOCKHOLM).astimezone(UTC)
        except ValueError:
            continue
    return None


def _date(raw: str) -> date | None:
    dt = _dt(raw)
    return dt.date() if dt else None


def _filing_id(row: dict) -> str:
    """FI gives no notification id, so synthesize a stable one from the row's identity
    (so re-running the import dedups instead of duplicating)."""
    parts = "|".join(
        _g(row, k)
        for k in (
            "Publication date",
            "Issuer",
            "Person discharging managerial responsibilities",
            "Notifier",
            "ISIN",
            "Transaction date",
            "Volume",
            "Price",
            "Nature of transaction",
        )
    )
    return "se-" + hashlib.sha1(parts.encode("utf-8")).hexdigest()[:18]


def row_to_filing(row: dict) -> ParsedFiling | None:
    issuer = _g(row, "Issuer")
    isin = _g(row, "ISIN")
    if not issuer or not isin:
        return None
    person = _g(row, "Person discharging managerial responsibilities") or _g(row, "Notifier")
    position = _g(row, "Position")
    nature = _g(row, "Nature of transaction")
    currency = _g(row, "Currency") or "SEK"
    notifier = _g(row, "Notifier")
    closely = _g(row, "Closely associated").lower() in ("yes", "ja", "true")

    tx = ParsedTransaction(
        seq=1,
        instrument_type=_g(row, "Intrument type") or _g(row, "Instrument type") or None,
        isin=isin,
        nature_raw=nature or None,
        transaction_type=map_transaction_type(nature),
        price=parse_decimal(_g(row, "Price")),
        currency=currency,
        volume=parse_decimal(_g(row, "Volume")),
        transaction_date=_date(_g(row, "Transaction date")),
        venue=_g(row, "Trading venue") or None,
        linked_to_option_programme=_g(row, "Linked to share option programme").lower()
        in ("yes", "ja", "true"),
    )

    out = ParsedFiling(
        filing_id=_filing_id(row),
        source="fi_sweden",
        country="SE",
        source_url=BASE + EXPORT_PATH,
        title=nature or "PDMR transaction",
        market="Nasdaq Stockholm",
        issuer_name=issuer,
        issuer_lei=_g(row, "LEI-code") or None,
        person_full_name=person or None,
        is_legal_person=closely and any(h in notifier.upper() for h in _LEGAL_HINT),
        position_status="Closely associated" if closely else "Relevant Person",
        role_raw=position or None,
        role_code=map_role_code(position),
        notification_type="amendment" if _g(row, "Amendment").lower() == "yes" else "initial",
        published_at=_dt(_g(row, "Publication date")),
        raw_text=";".join(f"{k.strip()}={_g(row, k)}" for k in row if k and k.strip()),
        transactions=[tx],
    )
    ok = bool(
        out.issuer_name and out.person_full_name and tx.transaction_date and tx.volume is not None
    )
    out.parse_status = "success" if ok else "partial"
    return out


def parse_csv(data: bytes) -> list[ParsedFiling]:
    """Decode FI's UTF-16 CSV export and map each row to a ParsedFiling."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")  # has a BOM
    elif b"\x00" in data[:200]:
        text = data.decode("utf-16-le")  # UTF-16 without BOM (FI's export)
    else:
        text = data.decode("utf-8-sig")
    rows = csv.DictReader(io.StringIO(text), delimiter=";")
    out = []
    for row in rows:
        filing = row_to_filing(row)
        if filing is not None:
            out.append(filing)
    return out


async def fetch_filings(client: PoliteClient, *, days: int = 14) -> list[ParsedFiling]:
    """Fetch recently-published PDMR transactions as ParsedFiling objects.

    ``days`` is informational for callers; the export endpoint returns the most recent page,
    which the daily/idempotent ingest dedups against the DB.
    """
    resp = await client.get(
        EXPORT_PATH + "?SearchFunctionType=Insyn&button=export&Page=1",
    )
    return parse_csv(resp.content)
