"""Nasdaq Nordic OAM source tests — offline against real MAR-template text + listing fixtures."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from scraper.nasdaq_nordic import (
    MARKET_COUNTRY,
    _dedupe,
    build_filing,
    parse_listing,
    parse_mar_body,
    parse_mar_text,
)

FIX = Path(__file__).parent / "fixtures"


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_copenhagen_english_template():
    f = parse_mar_text(_text("nasdaq_mar_copenhagen.txt"))
    assert f["person"] == "Carsten Rasch Egeriis"
    assert f["issuer"] == "Danske Bank A/S"
    assert f["lei"] == "MAES062Z21O4RZ2U7M96"
    assert f["isin"] == "DK0010274414"
    assert f["currency"] == "DKK"
    assert f["price"] == Decimal("0")
    assert f["volume"] == Decimal("2853")  # '2,853' English thousands
    assert f["txdate"] and f["txdate"].isoformat() == "2026-07-01"


def test_iceland_bilingual_template():
    f = parse_mar_text(_text("nasdaq_mar_iceland.txt"))
    assert f["person"] == "James C. Pelletier"
    assert f["issuer"] == "JBT Marel Corporation"
    assert f["lei"] == "5493007CT6ATBZ2L6826"
    assert f["isin"] == "US4778391049"
    assert f["currency"] == "USD"
    assert f["price"] == Decimal("145.00")
    assert f["volume"] == Decimal("409")
    assert f["txdate"] and f["txdate"].isoformat() == "2026-06-30"


def test_lithuania_local_language_still_yields_isin_and_date():
    # Lithuanian labels aren't anchored, but ISIN/date are language-independent.
    f = parse_mar_text(_text("nasdaq_mar_lithuania.txt"))
    assert f["isin"] == "LT0000132060"
    assert f["txdate"] and f["txdate"].isoformat() == "2026-06-30"


def test_market_country_map_excludes_sweden():
    assert "Main Market, Stockholm" not in MARKET_COUNTRY
    assert MARKET_COUNTRY["Main Market, Copenhagen"] == "DK"
    assert MARKET_COUNTRY["Main Market, Helsinki"] == "FI"
    assert MARKET_COUNTRY["First North Lithuania"] == "LT"


def test_build_filing_success_from_full_fields():
    f = parse_mar_text(_text("nasdaq_mar_copenhagen.txt"))
    item = {
        "disclosureId": 1450999,
        "company": "Danske Bank A/S",
        "market": "Main Market, Copenhagen",
        "headline": "Danske Bank A/S: Managers' transactions",
        "messageUrl": "https://view.news.eu.nasdaq.com/view?id=abc&lang=en",
        "releaseTime": "2026-07-01 18:40:00",
    }
    filing = build_filing(item, "DK", f, item["messageUrl"])
    assert filing.filing_id == "nasdaq-1450999"
    assert filing.country == "DK"
    assert filing.source == "nasdaq_nordic"
    assert filing.parse_status == "success"
    assert filing.issuer_name == "Danske Bank A/S"
    assert filing.person_full_name == "Carsten Rasch Egeriis"
    assert filing.published_at is not None
    tx = filing.transactions[0]
    assert tx.isin == "DK0010274414"
    assert tx.volume == Decimal("2853")
    assert tx.currency == "DKK"


def test_build_filing_partial_when_pdf_missing():
    item = {
        "disclosureId": 42,
        "company": "Some Oyj",
        "market": "Main Market, Helsinki",
        "headline": "Some Oyj: Managers' transactions",
        "messageUrl": "https://view.news.eu.nasdaq.com/view?id=xyz&lang=en",
        "releaseTime": "2026-07-01 09:00:00",
    }
    filing = build_filing(item, "FI", {}, item["messageUrl"])
    assert filing.parse_status == "partial"  # no person/isin/date
    assert filing.country == "FI"
    assert filing.issuer_name == "Some Oyj"  # still useful from the listing


def test_finland_inline_body_template():
    f = parse_mar_body(_text("nasdaq_body_finland.txt"))
    assert f["person"] == "Holm, Roger"
    assert f["issuer"] == "Wärtsilä Corporation"
    assert f["lei"] == "743700G7A9J1PHM3X223"
    assert f["isin"] == "FI0009003727"
    assert f["position"] == "Other senior manager"
    assert f["txdate"] and f["txdate"].isoformat() == "2026-07-01"
    assert f["venue"] is None  # 'Venue not applicable'
    assert f["rows"] == [(Decimal("0.00"), Decimal("6426"), "EUR")]


def test_build_filing_from_inline_body_is_success():
    f = parse_mar_body(_text("nasdaq_body_finland.txt"))
    item = {
        "disclosureId": 1450994,
        "company": "Wärtsilä Corporation",
        "market": "Main Market, Helsinki",
        "headline": "Wärtsilä Corporation - Manager's transaction: Holm, Roger",
        "messageUrl": "https://view.news.eu.nasdaq.com/view?id=b8&lang=en",
        "releaseTime": "2026-07-01 17:05:00",
    }
    filing = build_filing(item, "FI", f, item["messageUrl"])
    assert filing.parse_status == "success"
    assert filing.country == "FI"
    assert filing.person_full_name == "Holm, Roger"
    assert filing.transactions[0].isin == "FI0009003727"
    assert filing.transactions[0].volume == Decimal("6426")


def test_dedupe_prefers_english_twin():
    en = {
        "company": "Wärtsilä Corporation",
        "releaseTime": "2026-07-01 17:05:00",
        "market": "Main Market, Helsinki",
        "headline": "Wärtsilä Corporation - Manager's transaction: Holm, Roger",
        "language": "en",
        "disclosureId": 1450994,
    }
    fi = {
        "company": "Wärtsilä Oyj Abp",  # localized company name differs slightly
        "releaseTime": "2026-07-01 17:05:00",
        "market": "Main Market, Helsinki",
        "headline": "Wärtsilä Oyj Abp - Johdon liiketoimet: Holm, Roger",
        "language": "fi",
        "disclosureId": 1450993,
    }
    # same person + time + market -> collapses to one; english kept when company matches
    out = _dedupe([fi, en, en])
    langs = [i["language"] for i in out]
    assert "en" in langs


def test_parse_listing_from_fixture():
    payload = json.loads(_text("nasdaq_listing.json"))
    items = parse_listing(payload)
    assert len(items) == 6
    assert all(i["cnsCategory"] == "Managers' Transactions" for i in items)
    countries = {MARKET_COUNTRY.get(i["market"]) for i in items}
    assert countries - {None}  # at least one mapped country present
