"""France (AMF) source tests — offline against a real declaration PDF fixture."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from scraper.amf_fr import _fr_date, parse_amf_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "amf_fr_sample.pdf"


def test_parse_amf_pdf():
    f = parse_amf_pdf(
        FIXTURE.read_bytes(),
        meta={"filing_id": "fr-2026DD1123798", "issuer_name": "MEDIAN TECHNOLOGIES"},
    )
    assert f.parse_status == "success"
    assert f.country == "FR"
    assert f.source == "amf_fr"
    assert f.issuer_name == "MEDIAN TECHNOLOGIES"
    assert f.issuer_lei and len(f.issuer_lei) == 20
    assert f.person_full_name == "FREDRIK BRAG"
    assert f.role_code == "AD"  # CEO -> AD
    t = f.transactions[0]
    assert t.isin == "FR0011049824"
    assert t.currency == "EUR"
    assert t.price == Decimal("1.5000")
    assert t.volume == Decimal("84516.0000")
    assert t.transaction_date == date(2026, 6, 22)
    assert t.venue and "Euronext" in t.venue


def test_french_dates():
    assert _fr_date("22 juin 2026") == date(2026, 6, 22)
    assert _fr_date("1 décembre 2025") == date(2025, 12, 1)
    assert _fr_date("15 août 2026") == date(2026, 8, 15)
    assert _fr_date("not a date") is None


def test_french_nature_maps():
    from scraper.parser import map_transaction_type

    assert map_transaction_type("Souscription") == "A"
    assert map_transaction_type("Cession") == "D"
    assert map_transaction_type("Vente") == "D"
