"""Norway (Oslo Børs NewsWeb) source tests — offline against a real KRT-1500 form fixture."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from scraper.oslo_bors_no import build_filing, is_krt1500, parse_krt1500

FIX = Path(__file__).parent / "fixtures"


def _text() -> str:
    return (FIX / "oslo_krt1500.txt").read_text(encoding="utf-8")


def test_is_krt1500():
    assert is_krt1500(_text())
    assert not is_krt1500("some unrelated press release body")


def test_parse_krt1500_fields():
    f = parse_krt1500(_text())
    assert f["person"] == "Svend Egil LArsen"
    assert f["role"] == "CIO"
    assert f["issuer"] == "Nordic financials ASA"
    assert f["lei"] == "5967007LIEEXZXGCJS95"
    assert f["isin"] == "NO0013683409"
    assert f["nature"] == "Kjøp"
    assert f["currency"] == "NOK"
    assert f["price"] == Decimal("1.7188")
    assert f["volume"] == Decimal("29000")  # '29 000' space thousands
    assert f["txdate"] and f["txdate"].isoformat() == "2026-07-01"
    assert f["venue_mic"] == "XOAS"
    assert f["assoc_company"] == "SELACO AS"  # traded via associated company


def test_build_filing_success_and_buy_mapping():
    f = parse_krt1500(_text())
    item = {
        "messageId": 677483,
        "issuerName": "Nordic Financials ASA",
        "title": "Mandatory Notification of Trading by Primary Insiders",
        "publishedTime": "2026-07-01T14:56:00.000Z",
        "markets": ["XOSL"],
        "numbAttachments": 1,
    }
    filing = build_filing(item, f, "https://newsweb.oslobors.no/message/677483")
    assert filing.filing_id == "oslo-677483"
    assert filing.country == "NO"
    assert filing.source == "oslo_bors_no"
    assert filing.parse_status == "success"
    assert filing.person_full_name == "Svend Egil LArsen"
    assert filing.is_legal_person is True  # via SELACO AS
    assert filing.published_at is not None
    tx = filing.transactions[0]
    assert tx.transaction_type == "A"  # 'Kjøp' -> acquisition
    assert tx.isin == "NO0013683409"
    assert tx.price == Decimal("1.7188")
    assert tx.volume == Decimal("29000")


def test_build_filing_partial_when_no_form():
    item = {
        "messageId": 677492,
        "issuerName": "Zalaris ASA",
        "title": "Acceptances received under the mandatory offer ...",
        "publishedTime": "2026-07-01T21:20:44.142Z",
        "markets": ["XOSL"],
        "numbAttachments": 1,
    }
    filing = build_filing(item, {}, "https://newsweb.oslobors.no/message/677492")
    assert filing.parse_status == "partial"
    assert filing.country == "NO"
    assert filing.issuer_name == "Zalaris ASA"  # still useful from the listing
