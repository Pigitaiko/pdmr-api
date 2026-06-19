"""Parser tests against real Allegato 3F filings (ground truth documented in CLAUDE.md)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest

from scraper.parser import (
    map_role_code,
    map_transaction_type,
    parse_decimal,
    parse_filing,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fx(name: str) -> Path:
    return FIXTURES / name


# ---- primary fixture: CEMBRE 0088-10-2026 (exact ground truth) --------------------------------


def test_cembre_ground_truth():
    r = parse_filing(fx("cembre_0088-10-2026.pdf"), source_url="http://example/cembre.pdf")

    assert r.parse_status == "success"
    assert r.filing_id == "0088-10-2026"
    assert r.source == "emarketstorage"
    assert r.source_url == "http://example/cembre.pdf"
    assert r.title == "Comunicazione internal dealing"
    assert r.market == "Euronext Star Milan"
    assert r.tipologia == "3.1"
    assert r.notification_type == "initial"

    # issuer
    assert r.issuer_name == "CEMBRE SPA"
    assert r.issuer_lei == "8156008BFBC06F53DD56"

    # person (natural)
    assert r.is_legal_person is False
    assert r.person_first_name == "ALDO"
    assert r.person_last_name == "BOTTINI BONGRANI"
    assert r.person_full_name == "ALDO BOTTINI BONGRANI"
    assert r.position_status == "Relevant Person"
    assert r.role_raw == "Persona che esercita funzioni di amministrazione"
    assert r.role_code == "DIR"

    # published 19 Giugno 2026 09:25:28 Europe/Rome (CEST, +2) -> 07:25:28 UTC
    assert r.published_at == datetime(2026, 6, 19, 7, 25, 28, tzinfo=UTC)

    # two transaction rows, one per price/volume pair
    assert len(r.transactions) == 2
    for t in r.transactions:
        assert t.transaction_type == "D"
        assert t.nature_raw == "CESSIONE"
        assert t.instrument_type == "Azioni Ordinarie"
        assert t.isin == "IT0001128047"
        assert t.currency == "EUR"
        assert t.transaction_date == date(2026, 6, 18)
        assert t.time_from == time(10, 55, 0)
        assert t.time_to == time(11, 23, 0)
        assert t.venue == "BORSA ITALIANA S.P.A. - XMIL"
        assert t.venue_mic == "XMIL"
        assert t.linked_to_option_programme is False

    t1, t2 = r.transactions
    assert (t1.price, t1.volume) == (Decimal("92.5"), Decimal("950"))
    assert (t2.price, t2.volume) == (Decimal("93.1"), Decimal("650"))


# ---- cross-fixture coverage: layout variation across issuers ----------------------------------

EXPECTED = {
    "caltagirone_0083-15-2026.pdf": dict(
        filing_id="0083-15-2026",
        issuer="CALTAGIRONE SPA",
        legal=True,
        person="CHUPAS 2007 SRL",
        ttype="A",
        ntx=9,
        role="DIR",
    ),
    "italgas_0167-73-2026.pdf": dict(
        filing_id="0167-73-2026",
        issuer="ITALGAS SPA",
        legal=True,
        person="K STREET – ADVISOR & INVESTMENT SRL",
        ttype="A",
        ntx=1,
        role="MGMT",
    ),
    "emak_0115-15-2026.pdf": dict(
        filing_id="0115-15-2026",
        issuer="EMAK SPA",
        legal=False,
        person="Paolo Zambelli",
        ttype="D",
        ntx=11,
        role="DIR",
    ),
    "intesa_0033-139-2026.pdf": dict(
        filing_id="0033-139-2026",
        issuer="INTESA SANPAOLO SPA",
        legal=False,
        person="MASSIMO ENRICO PROVERBIO",
        ttype="D",
        ntx=1,
        role="MGMT",
    ),
    "sanlorenzo_2211-94-2026.pdf": dict(
        filing_id="2211-94-2026",
        issuer="SANLORENZO SPA",
        legal=False,
        person="Cecilia Maria Perotti",
        ttype="D",
        ntx=1,
        role="DIR",
    ),
    "tinexta_20053-93-2026.pdf": dict(
        filing_id="20053-93-2026",
        issuer="TINEXTA SPA",
        legal=True,
        person="Zinc BidCo SPA",
        ttype="O",
        ntx=1,
        role="DIR",
    ),
}


@pytest.mark.parametrize("fname,exp", EXPECTED.items())
def test_cross_fixture(fname, exp):
    r = parse_filing(fx(fname))
    assert r.parse_status == "success", f"{fname} parse_status={r.parse_status}"
    assert r.filing_id == exp["filing_id"]
    assert r.issuer_name == exp["issuer"]
    assert r.is_legal_person is exp["legal"]
    assert r.person_full_name == exp["person"]
    assert r.role_code == exp["role"]
    assert len(r.transactions) == exp["ntx"]
    assert all(t.transaction_type == exp["ttype"] for t in r.transactions)
    # LEI present and 20 chars; every tx priced and dated
    assert r.issuer_lei is not None and len(r.issuer_lei) == 20
    for t in r.transactions:
        assert t.price is not None and t.volume is not None
        assert t.isin is not None and t.isin.startswith("IT")
        assert t.transaction_date is not None


def test_all_fixtures_have_distinct_ids():
    ids = [parse_filing(fx(f)).filing_id for f in EXPECTED]
    ids.append(parse_filing(fx("cembre_0088-10-2026.pdf")).filing_id)
    assert len(set(ids)) == len(ids)


# ---- robustness: garbage input must not crash, must flag failed --------------------------------


def test_garbage_input_marks_failed(tmp_path):
    bad = tmp_path / "garbage.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a real pdf \x00\x01\x02 nonsense")
    r = parse_filing(bad)
    assert r.parse_status == "failed"
    assert r.transactions == []


def test_empty_file_marks_failed(tmp_path):
    bad = tmp_path / "empty.pdf"
    bad.write_bytes(b"")
    r = parse_filing(bad)
    assert r.parse_status == "failed"


# ---- unit tests for helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("92.5", Decimal("92.5")),
        ("0.911", Decimal("0.911")),
        ("45000", Decimal("45000")),
        ("1.234,56", Decimal("1234.56")),
        ("1.234.567", Decimal("1234567")),
        ("10,723", Decimal("10.723")),
        ("", None),
        (None, None),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw,code",
    [
        ("ACQUISTO", "A"),
        ("Acquisizione", "A"),
        ("Sottoscrizione", "A"),
        ("CESSIONE", "D"),
        ("Vendita", "D"),
        ("Altro / Other - pre-concordata", "O"),
        (None, "O"),
    ],
)
def test_map_transaction_type(raw, code):
    assert map_transaction_type(raw) == code


@pytest.mark.parametrize(
    "raw,code",
    [
        ("Amministratore Delegato", "AD"),
        ("Direttore Finanziario / CFO", "CFO"),
        ("Presidente del CdA", "CHAIR"),
        ("Consigliere di amministrazione", "DIR"),
        ("Persona che esercita funzioni di direzione", "MGMT"),
        ("Funzioni di controllo", "CTRL"),
        ("", "OTHER"),
    ],
)
def test_map_role_code(raw, code):
    assert map_role_code(raw) == code
