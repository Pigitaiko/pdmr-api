"""Netherlands — AFM register of managers' transactions (MAR Art. 19).

Two-step, like Italy but HTML instead of PDF:
1. ``/export.aspx?...&format=xml`` is a public index of every notification — id, transaction date,
   issuer (+ LEI), PDMR name, and role/function (richer than the CSV export).
2. each notification's detail page (``/details?id=<meldingid>``) carries the financial fields in a
   table: instrument type, ISIN, transaction category (nature), price, quantity, unit (currency),
   trading place.

We read the index, then fetch detail pages for the most recent ``limit`` notifications (the
idempotent ingest accumulates older ones over successive runs).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from xml.etree import ElementTree as ET
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

BASE = "https://www.afm.nl"
INDEX_PATH = "/export.aspx?type=0ee836dc-5520-459d-bcf4-a4a689de6614&format=xml"
DETAIL_PATH = (
    "/en/sector/registers/meldingenregisters/transacties-leidinggevenden-mar19-/details?id="
)
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

_LEGAL_HINT = (" N.V.", " B.V.", " NV", " BV", " S.A.", " AG", " LTD", "HOLDING")
_DETAIL_COLS = (
    "Instrument type", "ISIN", "Transaction category", "Transaction type",
    "Stock option program", "Trading place", "Price", "Quantity", "Unit",
)


class IndexRecord:
    __slots__ = ("meldingid", "date", "issuer", "lei", "person", "role", "closely")

    def __init__(self, el: ET.Element) -> None:
        g = lambda t: (el.findtext(t) or "").strip()  # noqa: E731
        self.meldingid = g("meldingid")
        self.date = _parse_date(g("transactiedatum"))
        self.issuer = g("uitgevendeinstelling")
        self.lei = g("lei") or None
        self.person = g("meldingsplichtige")
        self.role = g("functie")
        self.closely = bool(g("nauwgelieerdaan"))


def _parse_date(raw: str) -> date | None:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_index(xml: bytes) -> list[IndexRecord]:
    root = ET.fromstring(xml)
    return [IndexRecord(el) for el in root.findall("vermelding") if el.findtext("meldingid")]


def _cell_value(text: str, header: str) -> str:
    """Detail cells are responsive: each repeats its header, e.g. 'Price 0,00'. Strip it."""
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith(header):
        text = text[len(header) :].strip()
    return text


def parse_detail_transactions(html: str) -> list[ParsedTransaction]:
    """Parse the financial rows from an AFM detail page's transactions table."""
    soup = BeautifulSoup(html, "lxml")
    txs: list[ParsedTransaction] = []
    for tbl in soup.find_all("table"):
        heads = [th.get_text(" ", strip=True) for th in tbl.find_all("th")]
        if "ISIN" not in heads or "Quantity" not in heads:
            continue
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            vals = {}
            for i, td in enumerate(tds):
                header = heads[i] if i < len(heads) else ""
                vals[header] = _cell_value(td.get_text(" ", strip=True), header)
            nature = vals.get("Transaction category") or vals.get("Transaction type")
            txs.append(
                ParsedTransaction(
                    seq=len(txs) + 1,
                    instrument_type=vals.get("Instrument type") or None,
                    isin=(vals.get("ISIN") or None),
                    nature_raw=nature or None,
                    transaction_type=map_transaction_type(nature),
                    price=parse_decimal(vals.get("Price")),
                    currency=(vals.get("Unit") or "EUR").upper()[:8],
                    volume=parse_decimal(vals.get("Quantity")),
                    venue=vals.get("Trading place") or None,
                    linked_to_option_programme=(vals.get("Stock option program") or "").lower()
                    in ("yes", "ja"),
                )
            )
        break
    return txs


def build_filing(rec: IndexRecord, detail_html: str) -> ParsedFiling:
    txs = parse_detail_transactions(detail_html)
    for t in txs:
        if t.transaction_date is None:
            t.transaction_date = rec.date
    out = ParsedFiling(
        filing_id="nl-" + rec.meldingid,
        source="afm_nl",
        country="NL",
        source_url=BASE + DETAIL_PATH + rec.meldingid,
        title=rec.role or "Managers' transaction",
        market="Euronext Amsterdam",
        issuer_name=rec.issuer or None,
        issuer_lei=rec.lei,
        person_full_name=rec.person or None,
        is_legal_person=rec.closely and any(h in (rec.person or "").upper() for h in _LEGAL_HINT),
        position_status="Closely associated" if rec.closely else "Relevant Person",
        role_raw=rec.role or None,
        role_code=map_role_code(rec.role),
        notification_type="initial",
        published_at=(
            datetime.combine(rec.date, datetime.min.time(), AMSTERDAM).astimezone(UTC)
            if rec.date
            else None
        ),
        raw_text=detail_html[:4000],
        transactions=txs,
    )
    ok = bool(out.issuer_name and out.person_full_name and txs and all(
        t.volume is not None and t.transaction_date is not None for t in txs
    ))
    out.parse_status = "success" if ok else "partial"
    return out


async def fetch_filings(client: PoliteClient, *, limit: int = 60) -> list[ParsedFiling]:
    """Read the XML index, then fetch detail pages for the most recent ``limit`` notifications."""
    idx_resp = await client.get(INDEX_PATH)
    records = parse_index(idx_resp.content)[:limit]
    out: list[ParsedFiling] = []
    for rec in records:
        try:
            detail = await client.get(DETAIL_PATH + rec.meldingid)
            out.append(build_filing(rec, detail.text))
        except Exception:  # noqa: BLE001 - skip a bad detail page, keep the batch going
            continue
    return out
