"""Nasdaq Nordic — the OAM (Officially Appointed Mechanism) / message storage facility that
publishes managers' transactions (MAR Art. 19) for the whole Nasdaq Nordic + Baltic bloc.

One public JSON news API (``api.news.eu.nasdaq.com``) covers Helsinki, Copenhagen, Reykjavík,
Tallinn, Riga and Vilnius. Each disclosure links a detail page carrying a PDF attachment — the
EU-harmonised Art. 19 notification template (Commission Implementing Regulation 2016/523), the
same Annex the Italian Allegato 3F implements, so it parses on the numbered section *labels*.

Flow: query the news API filtered to ``cnscategory=Managers' Transactions`` -> per item, read the
detail page, pull the PDF attachment, parse the harmonised table. Issuer, market and publish time
come reliably from the listing; the PDF adds person, LEI, ISIN, nature, price and volume. When the
PDF is absent or in a language we don't anchor, we still emit a ``partial`` filing from the listing.

Sweden (Stockholm / First North Sweden) is intentionally *excluded* — it is already covered by the
Finansinspektionen source (``fi_sweden``); ingesting it here too would double-count.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pdfplumber

from scraper.http import PoliteClient
from scraper.parser import (
    ParsedFiling,
    ParsedTransaction,
    map_role_code,
    map_transaction_type,
    parse_decimal,
)

NEWS_API = "https://api.news.eu.nasdaq.com/news/query.action"
CET = ZoneInfo("CET")

# market label -> ISO country. Sweden omitted on purpose (covered by fi_sweden).
MARKET_COUNTRY: dict[str, str] = {
    "Main Market, Helsinki": "FI",
    "First North Finland": "FI",
    "Main Market, Copenhagen": "DK",
    "First North Denmark": "DK",
    "Main Market, Iceland": "IS",
    "First North Iceland": "IS",
    "Main Market, Tallinn": "EE",
    "First North Estonia": "EE",
    "Main Market, Riga": "LV",
    "First North Latvia": "LV",
    "Main Market, Vilnius": "LT",
    "First North Lithuania": "LT",
}

_ATTACH_RE = re.compile(r"https://attachment\.news\.eu\.nasdaq\.com/[0-9a-f]+")
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_LEI_RE = re.compile(r"\b([A-Z0-9]{18}[0-9]{2})\b")
_CCY_RE = re.compile(r"\b(EUR|USD|SEK|DKK|NOK|ISK|GBP|CHF|PLN)\b")
_MIC_RE = re.compile(r"\b(X[A-Z0-9]{3})\b")  # e.g. XLIT, XHEL, XCSE

# harmonised Art. 19 section labels (English). We anchor on these, tolerating a same-line or
# next-line value and PDF extraction artifacts like "P rice(s)".
_L_POSITION = re.compile(r"Position\s*/\s*status", re.I)
_L_NATURE = re.compile(r"Nature of the transaction", re.I)
_L_TXDATE = re.compile(r"Date of the transaction", re.I)
_L_PLACE = re.compile(r"Place of the transaction", re.I)
_L_INSTRUMENT = re.compile(r"Description of the financial", re.I)

_LEGAL = re.compile(r"\b(A/S|ASA|AB|PLC|OYJ|OY|HF|AS|SIA|UAB|SE|LTD|GMBH|HOLDING|CORP)\b", re.I)


def _parse_amount(tok: str) -> Decimal | None:
    """Parse one numeric token, deciding thousands vs decimal by shape.

    English-format thousands (``2,853`` / ``1,234,567.5``) -> strip commas. Otherwise defer to the
    European-aware :func:`parse_decimal` (handles ``9430,20`` and ``11.00373396``).
    """
    t = tok.strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        return parse_decimal(t.replace(",", ""))
    return parse_decimal(t)


def _value_after(text: str, label: re.Pattern[str], *, stop: int = 200) -> str | None:
    """Return the value for a harmonised label: rest of the label's line, else the next line."""
    m = label.search(text)
    if not m:
        return None
    line_end = text.find("\n", m.end())
    tail = text[m.end() : line_end if line_end != -1 else m.end() + stop].strip(" :\t")
    if tail:
        return tail[:stop]
    # value sits on the following non-empty line
    rest = text[line_end + 1 :] if line_end != -1 else ""
    for line in rest.splitlines():
        if line.strip():
            return line.strip()[:stop]
    return None


def _person_and_issuer(text: str) -> tuple[str | None, str | None]:
    """Both PDMR and issuer use an 'a) Name' label; split on the issuer section to disambiguate."""
    split = re.search(r"Details of the issuer", text, re.I)
    head = text[: split.start()] if split else text
    tail = text[split.start() :] if split else ""
    name_re = re.compile(r"a\)\s*Name", re.I)
    return _value_after(head, name_re), _value_after(tail, name_re)


_MONTHS = {
    m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)
}


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", raw)  # 2026-07-01 / 2026.06.30
    if m:
        y, mo, d = (int(x) for x in m.groups())
    elif m := re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", raw):  # 30.06.2026 / 05/09/2024
        d, mo, y = (int(x) for x in m.groups())
    elif m := re.search(r"(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})", raw):  # 5 September 2024
        d, mon, y = int(m.group(1)), m.group(2)[:3].lower(), int(m.group(3))
        mo = _MONTHS.get(mon, 0)
    else:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _price_volume(text: str) -> tuple[Decimal | None, Decimal | None, str]:
    """Pull (price, volume, currency) from the harmonised price/volume block (English templates)."""
    start = re.search(r"[Pp]\s?rice", text)
    if not start:
        return None, None, "EUR"
    end = _L_TXDATE.search(text, start.end())
    block = text[start.end() : end.start() if end else start.end() + 300]
    ccy_m = _CCY_RE.search(text[start.start() : (end.start() if end else start.end() + 300)])
    currency = ccy_m.group(1) if ccy_m else "EUR"
    for line in block.splitlines():
        stripped = _CCY_RE.sub(" ", line)
        nums = re.findall(r"-?\d[\d.,]*", stripped)
        vals = [v for v in (_parse_amount(n) for n in nums) if v is not None]
        if len(vals) >= 2:  # price then volume (harmonised column order)
            return vals[0], vals[1], currency
    return None, None, currency


def parse_mar_text(text: str) -> dict:
    """Extract harmonised Art. 19 fields from PDF text. Language-independent for ISIN/LEI/date."""
    person, issuer = _person_and_issuer(text)
    isin_m = _ISIN_RE.search(text)
    lei_m = _LEI_RE.search(text)
    nature = _value_after(text, _L_NATURE)
    price, volume, currency = _price_volume(text)
    # date: prefer the English label; fall back to the first date token (language-independent)
    txdate = _parse_date(_value_after(text, _L_TXDATE)) or _parse_date(text)
    place = _value_after(text, _L_PLACE)
    mic = _MIC_RE.search(place or "")
    return {
        "person": person,
        "issuer": issuer,
        "position": _value_after(text, _L_POSITION),
        "isin": isin_m.group(1) if isin_m else None,
        "lei": lei_m.group(1) if lei_m else None,
        "instrument": _value_after(text, _L_INSTRUMENT),
        "nature": nature,
        "price": price,
        "volume": volume,
        "currency": currency,
        "txdate": txdate,
        "venue": place,
        "venue_mic": mic.group(1) if mic else None,
    }


def _html_text(html: str) -> str:
    """Strip tags/entities and collapse whitespace so colon-labelled body fields sit inline."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


# Nasdaq inline body format (used mainly by Helsinki) — colon-delimited, order-independent.
_BODY_LABELS: list[tuple[str, str]] = [
    ("person", "Name:"),
    ("position", "Position:"),
    ("issuer", "Issuer:"),
    ("lei", "LEI:"),
    ("noti", "Notification type:"),
    ("ref", "Reference number:"),
    ("txdate", "Transaction date:"),
    ("venue", "Venue"),
    ("instrument", "Instrument type:"),
    ("isin", "ISIN:"),
    ("nature", "Nature of the transaction:"),
    ("nature", "Nature of transaction:"),  # some issuers drop 'the'
    ("details", "Transaction details"),
]
_DETAIL_RE = re.compile(r"Volume:\s*([\d.,]+)\s*Unit price:\s*([\d.,]+)\s*([A-Z]{3})", re.I)


def parse_mar_body(text: str) -> dict:
    """Parse the Nasdaq inline body format (colon-labelled). Returns fields incl. ``rows``."""
    marks: list[tuple[int, int, str]] = []
    for key, label in _BODY_LABELS:
        m = re.search(re.escape(label), text)
        if m:
            marks.append((m.start(), m.end(), key))
    marks.sort()
    vals: dict[str, str] = {}
    for i, (_, end, key) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        vals[key] = text[end:nxt].strip(" :\t-").strip()

    detail_block = vals.get("details", "")
    rows: list[tuple[Decimal | None, Decimal | None, str]] = []
    for vol, price, ccy in _DETAIL_RE.findall(detail_block):
        rows.append((_parse_amount(price), _parse_amount(vol), ccy.upper()))
    isin_m = _ISIN_RE.search(text)
    venue = vals.get("venue", "")
    return {
        "person": vals.get("person"),
        "issuer": vals.get("issuer"),
        "position": vals.get("position"),
        "isin": (isin_m.group(1) if isin_m else None) or (vals.get("isin") or None),
        "lei": vals.get("lei"),
        "instrument": vals.get("instrument"),
        "nature": vals.get("nature"),
        "txdate": _parse_date(vals.get("txdate")),
        "venue": None if venue.lower().startswith("not applicable") else (venue or None),
        "rows": rows,
    }


def build_filing(item: dict, country: str, fields: dict, source_url: str) -> ParsedFiling:
    """Assemble a ParsedFiling from listing metadata + parsed fields (partial-tolerant)."""
    issuer = item.get("company") or fields.get("issuer")
    person = fields.get("person")
    published = None
    rt = item.get("releaseTime") or item.get("published")
    if rt:
        try:
            published = (
                datetime.strptime(rt[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CET).astimezone(UTC)
            )
        except ValueError:
            published = None

    # rows: either the inline body's list, or the single PDF-template price/volume
    rows = fields.get("rows")
    if not rows:
        rows = [(fields.get("price"), fields.get("volume"), (fields.get("currency") or "EUR"))]
    nature = fields.get("nature")
    txns = [
        ParsedTransaction(
            seq=i + 1,
            instrument_type=fields.get("instrument"),
            isin=fields.get("isin"),
            nature_raw=nature,
            transaction_type=map_transaction_type(nature),
            price=price,
            currency=(ccy or "EUR")[:8],
            volume=volume,
            transaction_date=fields.get("txdate"),
            venue=fields.get("venue"),
            venue_mic=fields.get("venue_mic"),
        )
        for i, (price, volume, ccy) in enumerate(rows)
    ]
    position = fields.get("position")
    out = ParsedFiling(
        filing_id=f"nasdaq-{item.get('disclosureId')}",
        source="nasdaq_nordic",
        country=country,
        source_url=source_url or item.get("messageUrl"),
        title=item.get("headline") or "Managers' transaction",
        market=item.get("market"),
        issuer_name=issuer,
        issuer_lei=fields.get("lei"),
        person_full_name=person,
        is_legal_person=bool(person and _LEGAL.search(person)),
        position_status=position,
        role_raw=position,
        role_code=map_role_code(position),
        notification_type="initial",
        published_at=published,
        raw_text=(item.get("headline") or "")[:4000],
        transactions=txns,
    )
    ok = bool(issuer and person and fields.get("isin") and fields.get("txdate"))
    out.parse_status = "success" if ok else "partial"
    return out


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _query_url(limit: int) -> str:
    cat = quote("Managers' Transactions")
    return (
        f"{NEWS_API}?type=handleResponse&showAttachments=true&showCnsSpecific=true"
        f"&showCompany=true&displayLanguage=en&timeZone=CET&dateMask=yyyy-MM-dd+HH:mm:ss"
        f"&limit={limit}&start=0&dir=DESC&cnscategory={cat}"
    )


def parse_listing(payload: dict) -> list[dict]:
    items = payload.get("results", {}).get("item", [])
    if isinstance(items, dict):  # single result is not wrapped in a list
        items = [items]
    return items


def _headline_person(headline: str) -> str:
    """Best-effort person token from a headline like '... transaction: Holm, Roger'."""
    tail = headline.rsplit(":", 1)[-1].strip() if ":" in headline else ""
    return tail.lower() if "," in tail else ""


def _dedupe(items: list[dict]) -> list[dict]:
    """Collapse bilingual twins (same disclosure in two languages) — prefer the English rendering.

    Finnish issuers localize the company name across languages (``Wärtsilä Corporation`` vs
    ``Wärtsilä Oyj Abp``), so when the headline names a person we key on (time, market, person);
    otherwise (e.g. Danish filings without a person in the headline) we key on the legal name,
    which is identical across languages.
    """
    groups: dict[tuple, dict] = {}
    for it in items:
        when = it.get("releaseTime") or it.get("published")
        market = it.get("market")
        person = _headline_person(it.get("headline", ""))
        sig = (
            ("p", when, market, person)
            if person
            else ("c", when, market, (it.get("company") or "").strip().lower())
        )
        cur = groups.get(sig)
        if cur is None or (it.get("language") == "en" and cur.get("language") != "en"):
            groups[sig] = it
    return list(groups.values())


async def fetch_filings(client: PoliteClient, *, limit: int = 80) -> list[ParsedFiling]:
    """Query the Nasdaq Nordic news API for managers' transactions and parse each disclosure.

    A disclosure carries its Art. 19 data either as a PDF attachment (harmonised template — most
    main markets) or inline in the message body (Nasdaq's colon-labelled format — mainly Helsinki).
    """
    import json

    resp = await client.get(_query_url(limit))
    items = [
        i for i in parse_listing(json.loads(resp.text)) if MARKET_COUNTRY.get(i.get("market") or "")
    ]
    out: list[ParsedFiling] = []
    for item in _dedupe(items):
        country = MARKET_COUNTRY[item["market"]]
        fields: dict = {}
        source_url = item.get("messageUrl") or ""
        try:
            detail = (await client.get(source_url)).text
            # Prefer the inline colon-labelled body (cleanest, and richer than some scanned PDFs);
            # fall back to the attached harmonised-template PDF when the body isn't the inline form.
            body = parse_mar_body(_html_text(detail))
            if body.get("person") and body.get("isin"):
                fields = body
            else:
                am = _ATTACH_RE.search(detail)
                if am:
                    pdf_bytes = (await client.get(am.group(0))).content
                    fields = parse_mar_text(_pdf_text(pdf_bytes))
                else:
                    fields = body
        except Exception:  # noqa: BLE001 - fall back to a listing-only partial, keep the batch
            fields = {}
        out.append(build_filing(item, country, fields, source_url))
    return out
