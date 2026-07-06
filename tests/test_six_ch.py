"""Switzerland (SER) source tests — offline against a real management-transactions fixture."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from scraper.six_ch import parse_item, parse_overview

FIX = Path(__file__).parent / "fixtures"


def _payload() -> dict:
    return json.loads((FIX / "six_ch_overview.json").read_text(encoding="utf-8"))


def test_parse_overview_all_items():
    filings = parse_overview(_payload())
    assert len(filings) == len(_payload()["itemList"]) >= 3
    assert all(f.country == "CH" for f in filings)
    assert all(f.source == "six_ch" for f in filings)
    assert all(f.person_full_name is None for f in filings)  # Swiss law: no names
    assert all(f.transactions[0].currency == "CHF" for f in filings)


def test_buy_sell_and_legal_person_mapping():
    items = _payload()["itemList"]
    # fixture item 0 is a buy(1) by a related legal entity(L)
    f0 = parse_item(items[0])
    assert f0.transactions[0].transaction_type == "A"
    assert f0.is_legal_person is True
    assert f0.filing_id.startswith("ch-")
    # a sell(2) maps to D
    sells = [parse_item(i) for i in items if str(i.get("buySellIndicator")) == "2"]
    assert sells and all(f.transactions[0].transaction_type == "D" for f in sells)


def test_fields_and_status():
    f = parse_item(_payload()["itemList"][0])
    t = f.transactions[0]
    assert t.isin and t.isin.startswith("CH")
    assert t.price is not None and isinstance(t.price, Decimal)
    assert t.volume is not None
    assert t.transaction_date is not None
    assert f.role_code in ("DIR", "MGMT", "OTHER")
    assert f.parse_status == "success"
