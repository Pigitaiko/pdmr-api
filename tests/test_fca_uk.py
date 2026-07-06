"""UK (FCA NSM) source tests — offline against a real search response + RNS document fixture."""

from __future__ import annotations

import json
from pathlib import Path

from scraper.fca_uk import build_filing, pdmr_hits

FIX = Path(__file__).parent / "fixtures"


def test_pdmr_hits_filters_type():
    payload = json.loads((FIX / "fca_uk_search.json").read_text(encoding="utf-8"))
    recs = pdmr_hits(payload)
    assert len(recs) >= 1
    assert all(r["type"] == "Director/PDMR Shareholding" for r in recs)


def test_build_filing_parses_harmonised_rns():
    doc = (FIX / "fca_uk_rns.html").read_text(encoding="utf-8")
    rec = {
        "submitted_date": "2024-09-05T11:43:58Z",
        "lei": "213800K51Y9BZY7F9R69",
        "type": "Director/PDMR Shareholding",
        "headline": "PDMR Transaction",
        "download_link": "NSM/RNS/5337559.html",
        "disclosure_id": "5337559",
    }
    f = build_filing(rec, doc)
    assert f.country == "GB"
    assert f.source == "fca_uk"
    assert f.filing_id == "gb-5337559"
    assert f.person_full_name == "Adam Hansen"
    assert f.issuer_name == "On the Beach Group plc"
    assert f.issuer_lei == "213800K51Y9BZY7F9R69"
    assert f.position_status and "Strategy" in f.position_status
    assert f.published_at is not None
    assert f.parse_status in ("success", "partial")


def test_build_filing_partial_without_doc():
    rec = {
        "submitted_date": "2026-06-29T09:00:00Z",
        "lei": "213800ZSR3HVKMMPVG86",
        "type": "Director/PDMR Shareholding",
        "headline": "PDMR Transaction",
        "download_link": "NSM/RNS/abc.html",
    }
    f = build_filing(rec, None)
    assert f.parse_status == "partial"
    assert f.country == "GB"
    assert f.issuer_lei == "213800ZSR3HVKMMPVG86"  # from the search record
