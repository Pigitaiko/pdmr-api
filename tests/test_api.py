"""API tests against the real-fixture-seeded SQLite DB."""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_openapi_lists_all_endpoints(client):
    spec = (await client.get("/openapi.json")).json()
    paths = set(spec["paths"])
    for p in [
        "/health",
        "/v1/transactions",
        "/v1/transactions/{tx_id}",
        "/v1/issuers",
        "/v1/persons",
        "/v1/feed",
        "/v1/signals",
    ]:
        assert p in paths, f"missing {p}"


async def test_list_transactions_meta_and_shape(client):
    r = await client.get("/v1/transactions")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "meta"}
    # 26 transactions seeded across the 7 fixtures
    assert body["meta"]["total"] == 26
    assert body["meta"]["limit"] == 50
    assert len(body["data"]) == 26
    tx = body["data"][0]
    # money must be a string, not a float
    assert isinstance(tx["price"], str)
    assert isinstance(tx["signal_value"], str)
    # denormalised filing context present (dashboard contract)
    assert tx["issuer_name"]
    assert tx["role_code"]
    assert tx["filing_ref"]


async def test_filter_by_type_buys(client):
    r = await client.get("/v1/transactions", params={"type": "A"})
    body = r.json()
    # caltagirone (9) + italgas (1) = 10 acquisitions
    assert body["meta"]["total"] == 10
    assert all(t["transaction_type"] == "A" for t in body["data"])


async def test_filter_min_value(client):
    r = await client.get("/v1/transactions", params={"type": "A", "min_value": "50000"})
    body = r.json()
    assert body["meta"]["total"] >= 1
    for t in body["data"]:
        assert Decimal(t["signal_value"]) >= Decimal("50000")


async def test_filter_by_issuer_substring(client):
    r = await client.get("/v1/transactions", params={"issuer": "CEMBRE"})
    body = r.json()
    assert body["meta"]["total"] == 2  # CEMBRE has two price/volume rows


async def test_filter_by_role(client):
    r = await client.get("/v1/transactions", params={"role": "MGMT"})
    body = r.json()
    assert body["meta"]["total"] >= 1


async def test_pagination(client):
    p1 = (await client.get("/v1/transactions", params={"limit": 5, "offset": 0})).json()
    p2 = (await client.get("/v1/transactions", params={"limit": 5, "offset": 5})).json()
    assert len(p1["data"]) == 5
    ids1 = {t["id"] for t in p1["data"]}
    ids2 = {t["id"] for t in p2["data"]}
    assert ids1.isdisjoint(ids2)


async def test_get_transaction_by_id_and_404(client):
    listing = (await client.get("/v1/transactions", params={"limit": 1})).json()
    tx_id = listing["data"][0]["id"]
    ok = await client.get(f"/v1/transactions/{tx_id}")
    assert ok.status_code == 200
    assert ok.json()["id"] == tx_id

    missing = await client.get("/v1/transactions/99999999")
    assert missing.status_code == 404


async def test_bad_params_422(client):
    assert (await client.get("/v1/transactions", params={"limit": 0})).status_code == 422
    assert (await client.get("/v1/transactions", params={"min_value": "-5"})).status_code == 422
    assert (await client.get("/v1/transactions", params={"from": "not-a-date"})).status_code == 422


async def test_issuers_and_persons(client):
    issuers = (await client.get("/v1/issuers")).json()
    assert issuers["meta"]["total"] == 7
    assert all("filing_count" in i for i in issuers["data"])
    persons = (await client.get("/v1/persons")).json()
    assert persons["meta"]["total"] == 7


async def test_feed_since(client):
    full = (await client.get("/v1/feed")).json()
    assert full["meta"]["total"] == 7
    # since far future -> empty
    empty = (await client.get("/v1/feed", params={"since": "2099-01-01T00:00:00Z"})).json()
    assert empty["meta"]["total"] == 0


async def test_signals_csuite_buys(client):
    r = await client.get("/v1/signals", params={"min_value": "1000"})
    body = r.json()
    # caltagirone & italgas are acquisitions by DIR/MGMT roles; only DIR qualifies for signals
    for t in body["data"]:
        assert t["transaction_type"] == "A"
        assert Decimal(t["signal_value"]) >= Decimal("1000")
    # results ranked by signal_value desc
    vals = [Decimal(t["signal_value"]) for t in body["data"]]
    assert vals == sorted(vals, reverse=True)
