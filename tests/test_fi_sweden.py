"""Sweden (Finansinspektionen) structured-source adapter tests — offline against the CSV fixture."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from scraper.fi_sweden import parse_csv

FIXTURE = Path(__file__).parent / "fixtures" / "fi_sweden_sample.csv"


def _filings():
    return parse_csv(FIXTURE.read_bytes())


def test_csv_parses_all_rows_cleanly():
    filings = _filings()
    assert len(filings) > 100  # the sample window holds plenty of rows
    # structured source -> every row is a clean success
    assert all(f.parse_status == "success" for f in filings)
    # all from Sweden, priced in SEK
    assert all(f.country == "SE" and f.source == "fi_sweden" for f in filings)
    assert all(f.transactions[0].currency == "SEK" for f in filings)


def test_fields_mapped():
    f = next(f for f in _filings() if f.issuer_name)
    t = f.transactions[0]
    assert f.issuer_name
    assert f.person_full_name  # the PDMR name is native to this source
    assert f.role_code  # mapped from the English Position string
    assert t.isin and t.isin.startswith(("SE", "FI", "NO", "DK"))
    assert t.transaction_date is not None
    assert t.volume is not None
    assert t.transaction_type in ("A", "D", "O")


def test_filing_ids_unique_and_stable():
    filings = _filings()
    ids = [f.filing_id for f in filings]
    assert all(i and i.startswith("se-") for i in ids)
    assert len(set(ids)) == len(ids)  # unique -> idempotent ingest
    # deterministic: re-parsing yields the same ids
    assert ids == [f.filing_id for f in _filings()]


def test_buy_sell_classification_present():
    types = {f.transactions[0].transaction_type for f in _filings()}
    # the sample contains acquisitions and disposals, not only 'other'
    assert "A" in types


def test_acquisition_maps_to_buy():
    from scraper.parser import map_transaction_type

    assert map_transaction_type("Acquisition") == "A"
    assert map_transaction_type("Subscription") == "A"
    assert map_transaction_type("Disposal") == "D"
    assert map_transaction_type("Demerger increase") == "O"


def test_price_volume_are_decimals():
    f = next(f for f in _filings() if f.transactions[0].price is not None)
    assert isinstance(f.transactions[0].price, Decimal)
    assert isinstance(f.transactions[0].volume, Decimal)
