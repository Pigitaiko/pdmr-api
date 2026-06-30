"""Netherlands (AFM) source tests — offline against the index XML + detail HTML fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from scraper.afm_nl import build_filing, parse_detail_transactions, parse_index

FIX = Path(__file__).parent / "fixtures"


def test_parse_index_xml():
    recs = parse_index((FIX / "afm_nl_index.xml").read_bytes())
    assert len(recs) >= 10
    r = recs[0]
    assert r.meldingid
    assert r.issuer  # uitgevende instelling
    assert r.person  # meldingsplichtige
    assert r.role  # functie
    assert isinstance(r.date, date)


def test_parse_detail_transactions():
    txs = parse_detail_transactions((FIX / "afm_nl_detail.html").read_text(encoding="utf-8"))
    assert txs, "expected at least one transaction row in the detail table"
    t = txs[0]
    assert t.transaction_type in ("A", "D", "O")
    assert t.currency  # from the Unit column
    assert isinstance(t.volume, Decimal)
    # the fixture is a 'Verwerving' (acquisition) -> A
    assert t.nature_raw and "Verwerving" in t.nature_raw
    assert t.transaction_type == "A"


def test_build_filing_combines_index_and_detail():
    recs = parse_index((FIX / "afm_nl_index.xml").read_bytes())
    f = build_filing(recs[0], (FIX / "afm_nl_detail.html").read_text(encoding="utf-8"))
    assert f.country == "NL"
    assert f.source == "afm_nl"
    assert f.filing_id.startswith("nl-")
    assert f.issuer_name and f.person_full_name
    assert f.transactions
    assert f.parse_status in ("success", "partial")


def test_dutch_nature_maps():
    from scraper.parser import map_transaction_type

    assert map_transaction_type("Verwerving") == "A"
    assert map_transaction_type("Vervreemding") == "D"
