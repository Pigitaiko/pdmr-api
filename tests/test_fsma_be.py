"""Belgium (FSMA) source tests — offline against the listing + detail HTML fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from scraper.fsma_be import DETAIL_RE, parse_detail

FIX = Path(__file__).parent / "fixtures"


def test_listing_has_detail_links():
    html = (FIX / "fsma_be_listing.html").read_text(encoding="utf-8")
    slugs = {m.group(0).rsplit("/", 1)[-1] for m in DETAIL_RE.finditer(html)}
    assert len(slugs) >= 20  # the listing page holds many notifications


def test_parse_detail():
    f = parse_detail((FIX / "fsma_be_detail.html").read_text(encoding="utf-8"), "ab-inbev-277")
    assert f.parse_status == "success"
    assert f.country == "BE"
    assert f.source == "fsma_be"
    assert f.filing_id == "be-ab-inbev-277"
    assert f.issuer_name == "AB INBEV"
    assert f.person_full_name == "Garcia Claudio"
    assert f.role_code  # from Declarer Type
    t = f.transactions[0]
    assert t.isin == "US03524A1088"
    assert t.transaction_type == "D"  # 'Sale / Disposal'
    assert t.price == Decimal("82.55")
    assert t.currency == "EUR"
    assert t.volume == Decimal("98")
    assert t.transaction_date == date(2026, 6, 16)
    assert t.venue and "exchange" in t.venue.lower()
