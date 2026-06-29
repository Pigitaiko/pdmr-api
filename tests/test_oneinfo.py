"""1Info tests — parser adapter on real 1Info PDFs + offline API-client parsing.

1Info renders the Allegato 3F in several per-issuer templates. These fixtures cover the families
the parser handles cleanly: bilingual 'ALLEGATO/ANNEX' (OVS/SAFILO/KRUSO) and Italian-only
(DEXELANCE). Filings in other templates degrade to parse_status='partial' (raw_text retained),
never a crash — see DECISIONS D-009.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scraper.oneinfo import parse_listing, pdf_url
from scraper.parser import parse_filing

FIXTURES = Path(__file__).parent / "fixtures"


def _meta(filing_id: str, issuer: str, y: int, m: int, d: int) -> dict:
    return {
        "filing_id": filing_id,
        "issuer_name": issuer,
        "published_at": datetime(y, m, d, tzinfo=UTC),
        "title": "Internal dealing",
    }


ONEINFO_CASES = {
    "oneinfo_ovs_1709_169124.pdf": dict(
        meta=_meta("1709_169124_2026_oneinfo", "OVS S.p.A.", 2026, 6, 24),
        issuer="OVS S.p.A.",
        person="NICOLA PERIN",
        legal=False,
        isin="IT0005043507",
    ),
    "oneinfo_safilo_790_169059.pdf": dict(
        meta=_meta("790_169059_2026_oneinfo", "SAFILO GROUP", 2026, 6, 22),
        issuer="SAFILO GROUP",
        person="BALDIN VLADIMIRO",
        legal=False,
        isin="IT0004604762",
    ),
    "oneinfo_kruso_20308_169087.pdf": dict(
        meta=_meta("20308_169087_2026_oneinfo", "KRUSO KAPITAL S.p.A.", 2026, 6, 23),
        issuer="KRUSO KAPITAL S.p.A.",
        person="Garbifin S.r.l.",
        legal=True,
        isin="IT0005707341",
    ),
    "oneinfo_dexelance_2566_167955.pdf": dict(
        meta=_meta("2566_167955_2026_oneinfo", "Dexelance S.p.A.", 2026, 5, 18),
        issuer="Dexelance S.p.A.",
        person="Giorgio Gobbi",
        legal=False,
        isin="IT0005703787",
    ),
}


@pytest.mark.parametrize("fname,exp", ONEINFO_CASES.items())
def test_oneinfo_fixture_parses(fname, exp):
    r = parse_filing(FIXTURES / fname, source="oneinfo", meta=exp["meta"])
    assert r.parse_status == "success", f"{fname} -> {r.parse_status}"
    assert r.source == "oneinfo"
    assert r.filing_id == exp["meta"]["filing_id"]
    assert r.issuer_name == exp["issuer"]
    assert r.issuer_lei is not None and len(r.issuer_lei) == 20
    assert r.person_full_name == exp["person"]
    assert r.is_legal_person is exp["legal"]
    assert r.published_at == exp["meta"]["published_at"]
    assert r.transactions
    for t in r.transactions:
        assert t.isin == exp["isin"]
        assert t.price is not None and t.volume is not None
        assert t.transaction_date is not None
        assert t.currency == "EUR"


def test_oneinfo_volume_thousands_grouping():
    # DEXELANCE: 'Euro 0,600 460.634' -> price 0.600, volume 460634 (dot = thousands sep)
    r = parse_filing(
        FIXTURES / "oneinfo_dexelance_2566_167955.pdf",
        source="oneinfo",
        meta=ONEINFO_CASES["oneinfo_dexelance_2566_167955.pdf"]["meta"],
    )
    t = r.transactions[0]
    assert t.price == Decimal("0.600")
    assert t.volume == Decimal("460634")
    assert t.venue_mic == "EXM"


# ---- offline API-client parsing (no network) -------------------------------------------------


def test_pdf_url_extracts_year_from_id():
    url = pdf_url("1709_169124_2026_oneinfo")
    assert "year=2026" in url
    assert "file=1709_169124_2026_oneinfo.pdf" in url
    assert url.startswith("https://www.1info.it/PdfViewer/PdfShow.aspx")


def test_parse_listing_filters_internal_dealing():
    payload = json.loads((FIXTURES / "oneinfo_listing.json").read_text(encoding="utf-8"))
    items = parse_listing(payload)
    assert items, "expected internal-dealing rows in the saved listing sample"
    for it in items:
        assert it.source == "oneinfo"
        assert "internal dealing" in (it.title or "").lower()
        assert it.url.startswith("https://www.1info.it/PdfViewer/PdfShow.aspx")
        assert it.filing_id and it.meta and it.meta["filing_id"] == it.filing_id
