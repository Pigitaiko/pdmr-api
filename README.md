# PDMR Insider Transactions API

Machine-readable feed of **Italian PDMR insider-dealing filings** — the mandatory public
disclosures that company insiders ("Persons Discharging Managerial Responsibilities") must make
under **Article 19 of the Market Abuse Regulation, (EU) 596/2014**. This is the European analogue
of the SEC EDGAR Form 4 feed, which today does not exist in machine-readable form for Italy.

The platform scrapes the standardised **Allegato 3F** notification PDFs from public sources,
parses them into structured records, stores them in PostgreSQL, and serves them through a
documented REST API.

> **Legal basis & boundary.** These filings are mandatory *public* disclosures under Art. 19 MAR.
> We parse individual public regulatory filings; we do **not** redistribute any operator's
> database. The API surfaces public data only — it is **not** investment advice and provides no
> trade execution or brokerage service.

## Quickstart

```bash
git clone <repo> && cd pdmr-api
cp .env.example .env          # then put a real contact email in USER_AGENT
docker compose up -d          # postgres + redis (healthchecked)
make migrate                  # apply the Alembic schema
make seed                     # load the canonical CEMBRE fixture (optional)
make scrape                   # ingest a real batch from eMarketStorage
make serve                    # http://localhost:8000/docs
```

Bring up the **whole stack** (Postgres + Redis + API + scheduler) in one command:

```bash
docker compose --profile full up -d
```

## Example requests

```bash
curl 'http://localhost:8000/v1/transactions?type=A&min_value=50000'
curl 'http://localhost:8000/v1/transactions?issuer=CEMBRE'
curl 'http://localhost:8000/v1/signals?min_value=50000'     # C-suite open-market buys
curl 'http://localhost:8000/v1/feed?since=2026-06-01T00:00:00Z'
```

Money values are returned as **strings** (decimal-safe), timestamps as **ISO-8601 UTC**.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/v1/transactions` | filters: `issuer, from, to, type, role, min_value, source, limit, offset` |
| GET | `/v1/transactions/{id}` | 404 if unknown |
| GET | `/v1/issuers` | with filing counts |
| GET | `/v1/persons` | with filing counts |
| GET | `/v1/feed?since=<ts>` | filings ingested after a timestamp (downstream polling) |
| GET | `/v1/signals` | open-market C-suite buys, ranked by value |
| GET | `/docs` | auto OpenAPI UI |

## Environment

| var | meaning |
|---|---|
| `DATABASE_URL` | async SQLAlchemy URL, e.g. `postgresql+asyncpg://pdmr:pdmr@localhost:5432/pdmr` |
| `REDIS_URL` | `redis://localhost:6379/0` (optional; dedup + rate-limit degrade gracefully if absent) |
| `LOG_LEVEL` | `INFO` etc. |
| `USER_AGENT` | descriptive UA sent to sources; put a real reachable contact here |

## Project structure

```
scraper/   parser.py · emarketstorage.py · oneinfo.py · http.py · ingest.py · scheduler.py
api/       main.py · routes.py · deps.py
database.py · models.py · schemas.py · config.py · seed.py
alembic/   env.py · versions/
tests/     test_parser.py · test_api.py · test_scraper.py · test_scheduler.py · fixtures/
static/    index.html (minimal dashboard)
docker-compose.yml · Dockerfile · Makefile · pyproject.toml · CLAUDE.md · DECISIONS.md
```

## Development

```bash
uv sync          # create venv + install (uv installs Python 3.11 if needed)
make test        # pytest
make lint        # ruff + mypy
make format      # ruff format + autofix
make scrape      # python -m scraper.ingest
make backfill YEAR=2026
```

The parser is the high-value core: it locates Allegato 3F **section labels** (never fixed
coordinates) so it tolerates layout drift, handles Italian-only and bilingual IT/EN forms,
natural and legal persons, multiple price/volume rows, and free grants. Imperfect parses are
flagged `parse_status='partial'` / `'failed'` (with the raw text preserved) rather than crashing
the pipeline. Tests assert exact ground truth against real filings in `tests/fixtures/`.

## Sources & politeness

- **eMarketStorage** (Teleborsa) — server-rendered, fully implemented.
- **1Info** (Computershare) — a Vue SPA; stubbed pending a JSON-API integration (no headless
  browsers; see `DECISIONS.md` D-007).

The scraper sends a descriptive `User-Agent`, throttles to ≤1 request/second per source, honours
`robots.txt`, and backs off on HTTP 429. Ingestion is idempotent (dedup on filing id).

See **`CLAUDE.md`** for the verified schema and **`DECISIONS.md`** for the engineering decisions
(including why some checks run against SQLite in environments without Docker).
