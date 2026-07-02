# PDMR — European Insider-Transactions API

**Live demo → https://pdmr-api.onrender.com**
· [Dashboard](https://pdmr-api.onrender.com/dashboard) · [API docs](https://pdmr-api.onrender.com/docs) · [JSON](https://pdmr-api.onrender.com/v1/signals)

A machine-readable feed of **European PDMR insider-dealing filings** — the mandatory public
disclosures that company insiders ("Persons Discharging Managerial Responsibilities") and their
close associates must make under **Article 19 of the Market Abuse Regulation, (EU) 596/2014**. It's
the European analogue of the SEC EDGAR Form 4 feed, which today does not exist as one clean,
cross-border, machine-readable source.

Each country publishes the same Art. 19 disclosure in a different way — PDF, CSV, XML, JSON, or a
regulator/exchange portal. This platform integrates each source once, **normalizes every filing
into one schema** (with a `country` and native `currency` on every record), stores it in
PostgreSQL, and serves it through a documented REST API.

> **Legal basis & boundary.** These are mandatory *public* disclosures under Art. 19 MAR. The
> platform parses individual public regulatory filings; it does **not** redistribute any operator's
> database. It surfaces public data only — it is **not** investment advice and provides no trade
> execution or brokerage/MiFID-II service.

## Coverage — 12 markets

| Market | Source | Format |
|---|---|---|
| 🇮🇹 Italy | eMarketStorage + 1Info | Allegato 3F PDFs |
| 🇸🇪 Sweden | Finansinspektionen insynsregister | CSV (SEK) |
| 🇳🇱 Netherlands | AFM register | XML index + HTML detail |
| 🇫🇷 France | AMF BDIF | JSON API + PDFs |
| 🇧🇪 Belgium | FSMA register | server-rendered HTML |
| 🇫🇮 🇩🇰 🇮🇸 🇪🇪 🇱🇻 🇱🇹 Nordics & Baltics | Nasdaq Nordic OAM | one JSON news API + harmonized Art. 19 template |
| 🇳🇴 Norway | Oslo Børs NewsWeb OAM | JSON API + Finanstilsynet KRT-1500 form |

The Art. 19 notification form is EU-harmonized (Commission Implementing Regulation 2016/523), so the
core parser is largely reusable across markets — the per-country work is the **source** integration.
Adding a country = one adapter registered in `scraper/ingest.py`. See `DECISIONS.md` for the
per-country source notes and what's currently blocked (DE robots, UK WAF, ES/PT SPAs).

## Example requests

```bash
# Open-market C-suite buy signals over €10k, ranked by value
curl 'https://pdmr-api.onrender.com/v1/signals?min_value=10000'

# All Norwegian managers' transactions
curl 'https://pdmr-api.onrender.com/v1/transactions?country=NO'

# Buys only, filtered by issuer
curl 'https://pdmr-api.onrender.com/v1/transactions?type=A&issuer=danske'

# Poll everything ingested since a timestamp (downstream sync)
curl 'https://pdmr-api.onrender.com/v1/feed?since=2026-06-01T00:00:00Z'
```

Money values are returned as **strings** (decimal-safe), timestamps as **ISO-8601 UTC**, and every
transaction carries its own `currency` and `country`.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/v1/transactions` | filters: `country, issuer, from, to, type, role, min_value, source, limit, offset` |
| GET | `/v1/transactions/{id}` | 404 if unknown |
| GET | `/v1/signals` | open-market C-suite buys, ranked by value; supports `country`, `min_value` |
| GET | `/v1/issuers` · `/v1/persons` | with filing counts |
| GET | `/v1/feed?since=<ts>` | filings ingested after a timestamp (downstream polling) |
| GET | `/docs` | auto OpenAPI UI |

## Run it locally

```bash
git clone https://github.com/Pigitaiko/pdmr-api && cd pdmr-api
cp .env.example .env          # USER_AGENT already points at this repo; swap in a contact if you like
docker compose up -d          # postgres (healthchecked)
make migrate                  # apply the Alembic schema
make scrape SOURCE=all        # ingest a live batch across all 12 markets
make serve                    # http://localhost:8000  (landing) · /dashboard · /docs
```

## Deploy (Render, free tier)

The repo ships a `render.yaml` blueprint (Postgres + a Docker web service that migrates on boot and
bootstraps real data on first start). See **`DEPLOY.md`** — one-click **New → Blueprint → Apply**.

## Development

```bash
uv sync            # venv + install (installs Python 3.11 if needed)
make test          # pytest (90 tests, offline against real fixtures)
make lint          # ruff + mypy
make format        # ruff format + autofix
```

## Architecture

```
scraper/   parser.py            shared Art. 19 helpers + Italian Allegato 3F PDF parser
           emarketstorage.py · oneinfo.py           IT
           fi_sweden.py · afm_nl.py · amf_fr.py · fsma_be.py   SE · NL · FR · BE
           nasdaq_nordic.py     FI/DK/IS/EE/LV/LT (one OAM adapter)
           oslo_bors_no.py      NO (KRT-1500 + harmonized template)
           http.py              polite async client (UA, ≤1 req/s, robots, 429 backoff)
           ingest.py            orchestration (idempotent upsert, per-source isolation)
           scheduler.py         APScheduler market-hours cadence (optional worker)
api/       main.py · routes.py · deps.py
database.py · models.py · schemas.py · config.py
alembic/   env.py · versions/
static/    landing.html (marketing) · index.html (dashboard — same design system)
tests/     parser, API, and per-source tests against real fixtures
```

Each source adapter returns a normalized `ParsedFiling`; ingestion dedupes on filing id (idempotent)
and isolates per-source failures so one bad source never aborts a batch. The parsers anchor on
document **labels** (never fixed coordinates), tolerate layout/language drift, and downgrade to
`parse_status='partial'`/`'failed'` (preserving raw text) rather than crashing.

## Sources & politeness

The scraper sends a descriptive `User-Agent`, throttles to **≤1 request/second per source**, honours
`robots.txt`, and backs off on HTTP 429. No PDF binaries are stored (URL + extracted text only).

See **`CLAUDE.md`** for the verified schema and **`DECISIONS.md`** for the engineering decision log.
