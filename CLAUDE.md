# CLAUDE.md — PDMR Ingestion Platform (source of truth)

> **Provenance note.** This file was authored by the build agent on 2026-06-19 because no
> pre-existing CLAUDE.md was present in the environment (see `DECISIONS.md` D-001). Every
> "verified" fact below was extracted from **real** Allegato 3F filings downloaded from
> eMarketStorage, not invented. The originally-referenced fixture "Nexi 2170-25-2026" did not
> exist in this environment; the canonical fixture is therefore the real **CEMBRE `0088-10-2026`**
> filing, with six more real filings from other issuers for layout coverage.

## What this system is

Ingests **PDMR** (Persons Discharging Managerial Responsibilities) insider-transaction filings —
mandatory disclosures under **Art. 19 MAR, Regulation (EU) 596/2014** — from public sources across
**multiple European markets**, normalises them into one schema, stores them in PostgreSQL, and serves
them via a documented REST API. EU analogue of the SEC EDGAR Form 4 feed.

**Live coverage (2026-07-01): 🇮🇹 Italy · 🇸🇪 Sweden · 🇳🇱 Netherlands · 🇫🇷 France · 🇧🇪 Belgium ·
🇫🇮 Finland · 🇩🇰 Denmark · 🇮🇸 Iceland · 🇪🇪 Estonia · 🇱🇻 Latvia · 🇱🇹 Lithuania — 11 markets.** Each
country is a pluggable source adapter normalising to the same `ParsedFiling`; every filing carries a
`country`. Italy is PDF (the original Allegato 3F parser); the others are structured sources (CSV /
XML+HTML / JSON-API+PDF). The six Nordic/Baltic markets come from a **single** adapter
(`scraper/nasdaq_nordic.py`) over Nasdaq Nordic's OAM news API — the biggest single unlock, since the
data is the EU-harmonised Art. 19 template (same Annex as the Allegato 3F). Adding a country = one
adapter + register it in `scraper/ingest.py`. See DECISIONS D-009..D-015 and PROGRESS.md for the
per-country source notes and what's blocked (DE/UK/ES).

Scope boundary: **layer 1 only** — data ingestion + API. No trade execution, no brokerage, no
MiFID-II investment service. We surface public regulatory data; we do not give investment advice.

Sources:
- **eMarketStorage** (Teleborsa) — `https://www.emarketstorage.it` — server-rendered, paginated
  with `?page=N`. Press releases: `/en/node/21`. Regulated documents: `/en/node/30`.
- **1Info** (Computershare) — `https://www.1info.it` — implemented via its JSON API (no headless
  browser): `POST /PORTALE1INFO/API/Comunicati` for the listing, `/PdfViewer/PdfShow.aspx` for
  PDFs. 1Info uses several per-issuer renderings of the Allegato 3F, so the parser is section-based
  and partial-tolerant (~47% clean `success` on a live batch; rest `partial` with raw_text kept).
  See `DECISIONS.md` D-009/D-010.

## Verified Allegato 3F structure (from real PDFs)

A filing PDF has a **cover page** (eMarketStorage "Informazione Regolamentata") followed by the
**bilingual IT/EN Allegato 3F** template (2–3 pages). The cover page yields:

| Cover field | Example (CEMBRE) | Maps to |
|---|---|---|
| `Regolamentata n.` / market line | `Euronext Star Milan` | `filings.market` |
| `…n. <ID>` and footer `Fine Comunicato n.<ID>` | `0088-10-2026` | **`filings.filing_id` (dedup key)** |
| `Societa' :` | `CEMBRE` | issuer short name |
| `Tipologia :` | `3.1` | `filings.tipologia` |
| `Data/Ora Inizio Diffusione` | `19 Giugno 2026 09:25:28` | `filings.published_at` (Europe/Rome→UTC) |
| `Oggetto :` | `Comunicazione internal dealing` | `filings.title` |

The **filing ID format is `\d+-\d+-\d{4}`** (e.g. `0088-10-2026`, `0033-139-2026`, `20053-93-2026`).
This is the canonical idempotency key.

Allegato 3F sections (anchor parsing on these labels, never fixed coordinates):

1. **Person** — `Dati relativi alla persona…`. Natural person → `Nome:` / `Cognome:`
   (`Per le persone fisiche`). Legal person → `Denominazione completa…` (`Per le persone giuridiche`).
2. **Reason** — `Motivo della notifica`. `Posizione / Qualifica` = `Persona Rilevante` (relevant) or
   `Persona Strettamente Associata` (closely associated). `Ruolo:` = free-text role. `Notifica
   iniziale` vs `Modifica` (initial/amendment).
3. **Issuer** — `Dati relativi all'emittente`. `Nome completo dell'entità:` (e.g. `CEMBRE SPA`),
   `LEI` 20-char code (e.g. `8156008BFBC06F53DD56`).
4. **Transaction(s)** — repeatable block `Operazione - N` / `Transaction - N`:
   - a) `Tipo di strumento` / `Description of the financial instrument` → e.g. `Azioni Ordinarie`;
     `ISIN:` (e.g. `IT0001128047`).
   - b) `Natura dell'operazione` → free text: `ACQUISTO`, `CESSIONE`, `Altro / Other - …`.
     Also a yes/no: linked to share-option programme.
   - c) `Prezzo/i e Volume/i` → **one or more `<price> EUR <volume>` pairs** (same operation, same
     day/venue, multiple fills). Prices can be sub-€1 with 3–4 decimals (e.g. `0.911 EUR 10000`).
   - d) `Informazioni aggregate` (aggregated price/volume) — often empty.
   - e) `Data dell'operazione` → `YYYY-MM-DD From: HH:MM:SS To: HH:MM:SS` (ISO-8601, UTC).
   - f) `Luogo dell'operazione` → venue, e.g. `BORSA ITALIANA S.P.A. - XMIL` (MIC = `XMIL`).

### Canonical ground truth — CEMBRE `0088-10-2026` (`tests/fixtures/cembre_0088-10-2026.pdf`)
- issuer: name `CEMBRE SPA`, LEI `8156008BFBC06F53DD56`
- person: natural, first `ALDO`, last `BOTTINI BONGRANI`; status `Persona Rilevante`;
  ruolo `Persona che esercita funzioni di amministrazione` → role_code `DIR`
- market `Euronext Star Milan`; tipologia `3.1`; published_at `2026-06-19 09:25:28` Europe/Rome
- notification: initial (`Nuova notifica`)
- 1 operation, instrument `Azioni Ordinarie`, ISIN `IT0001128047`, nature `CESSIONE` →
  transaction_type `D`, not linked to option programme, date `2026-06-18` 10:55:00–11:23:00,
  venue `BORSA ITALIANA S.P.A. - XMIL`
- **2 transaction rows** (one per price/volume pair): `(92.5, 950)` and `(93.1, 650)` EUR →
  signal_value `87 875.00` and `60 515.00`.

## Database schema (PostgreSQL 15; SQLAlchemy 2.x async / asyncpg)

Money = `NUMERIC`, never float. Timestamps stored UTC, `timezone=True`.

**issuers**: `id` PK · `name` (full legal, e.g. `CEMBRE SPA`) · `short_name` · `lei` (unique, nullable)
· `created_at`/`updated_at`. Dedup on `lei` if present, else `name`.

**persons**: `id` PK · `full_name` (unique) · `first_name` · `last_name` · `is_legal_person` bool
· timestamps. Dedup on `full_name`.

**filings**: `id` PK · `filing_id` text **UNIQUE** (the `NNNN-NN-YYYY` id) · `issuer_id` FK ·
`person_id` FK · `source` (`emarketstorage`|`oneinfo`) · `source_url` · `title` · `market` ·
`tipologia` · `position_status` · `role_raw` · `role_code` · `notification_type`
(`initial`|`amendment`) · `published_at` tz · `ingested_at` tz default now · `parse_status`
(`success`|`partial`|`failed`) · `raw_text` · timestamps.

**transactions**: `id` PK · `filing_id` FK→filings.id · `seq` int · `instrument_type` ·
`isin` · `nature_raw` · `transaction_type` (`A`|`D`|`O`) · `price` NUMERIC(20,6) nullable ·
`currency` default `EUR` · `volume` NUMERIC(20,4) nullable ·
**`signal_value` NUMERIC GENERATED ALWAYS AS (price*volume) STORED** · `transaction_date` date ·
`time_from`/`time_to` time nullable · `venue` · `venue_mic` · `linked_to_option_programme` bool ·
`created_at`.

Indexes: `filings.filing_id` unique; `filings(issuer_id)`, `(person_id)`, `(published_at)`,
`(parse_status)`, `(source)`; `transactions(filing_id)`, `(transaction_type)`,
`(transaction_date)`, `(signal_value)`, `(isin)`; `issuers.lei` unique; `persons.full_name` index.

### Code mappings
- **transaction_type**: `ACQUISTO|ACQUISIZIONE|ACQUISTO|SOTTOSCRIZIONE|SUBSCRIPTION|PURCHASE` → `A`;
  `CESSIONE|VENDITA|SALE|DISPOSAL` → `D`; anything else (incl. `Altro/Other`) → `O`.
- **role_code** (from `Ruolo`/role text, case-insensitive): contains `amministratore delegato`/`chief
  executive`/`ad ` → `AD`; `direttore finanziario`/`cfo`/`chief financial` → `CFO`; `presidente`/
  `chair` → `CHAIR`; `consigliere`/`amministrazione`/`administration`/`board` → `DIR`; `direzione`/
  `management` → `MGMT`; `controllo`/`control` → `CTRL`; closely-associated only → `CAP`; else `OTHER`.
- **parse_status**: `success` = person + issuer + ≥1 fully-priced transaction; `partial` = filing
  identifiable but some required field missing (set it null, don't crash); `failed` = unparseable
  (still insert filing row with `raw_text`).

## REST API (FastAPI, async)

- `GET /health`
- `GET /v1/transactions` — filters: `issuer` (name/lei substring), `from`,`to` (transaction_date),
  `type` (A/D/O), `role` (role_code), `min_value` (signal_value), `source`, `limit`, `offset`.
  Response: `{ data: [...], meta: { total, limit, offset } }`.
- `GET /v1/transactions/{id}` — 404 if absent.
- `GET /v1/issuers`, `GET /v1/persons` — list + counts.
- `GET /v1/feed?since=<iso-ts>` — filings with `ingested_at > since`, for downstream polling.
- `GET /v1/signals` (Phase 6) — `transaction_type=A AND price>0 AND role_code IN (AD,CFO,CHAIR,DIR)
  AND signal_value>=50000`, ranked by signal_value desc.
- Money serialised as **strings** (decimals) in JSON; timestamps ISO-8601 UTC. Per-IP Redis token
  bucket rate limit; permissive CORS. Auto `/docs`.

## Conventions
- Python 3.11+, async throughout. `uv` for env/deps. Ruff (lint+format) + mypy. structlog.
- No Selenium/headless browsers. No PDF binaries in Postgres (store URL + extracted text only).
- Politeness: ≤1 req/s per source, `User-Agent` `PDMR-API-bot/0.1 (+contact@…)`, honour robots.txt,
  back off on HTTP 429. Idempotent ingest: dedup on `filing_id`; track seen URLs in Redis SET.
- Conventional commits. Commit after every green test run.

## Repo layout
```
scraper/   parser.py · emarketstorage.py · oneinfo.py · ingest.py · scheduler.py
api/       main.py · routes.py · deps.py
database.py · models.py · schemas.py · config.py · seed.py
alembic/   env.py · versions/
tests/     test_parser.py · test_api.py · fixtures/*.pdf
docker-compose.yml · Makefile · pyproject.toml · .env.example · .pre-commit-config.yaml
```

## Current status
- [x] Phase 0 — scaffolding (uv, dirs, pyproject, env, gitignore, docker-compose, Makefile,
      pre-commit). Lint clean. Compose authored; not run here (no Docker — DECISIONS D-002).
- [x] Phase 1 — DB layer: models, async engine, idempotent store, seed, Alembic initial
      migration (applies cleanly on SQLite; same migration targets Postgres).
- [x] Phase 2 — PDF parser (core): 32 tests; exact CEMBRE ground truth + 6 cross-issuer fixtures.
- [x] Phase 3 — scrapers: eMarketStorage implemented, 1Info stubbed (SPA). LIVE-verified on
      SQLite: 91 filings, 96.7% success, 2353 tx; re-run ingests 0 (idempotent).
- [x] Phase 4 — REST API: all endpoints + filters + meta; 13 API tests; decimals as strings.
- [x] Phase 5 — scheduler (APScheduler, market-hours cadence), Dockerfile, combined compose
      (`--profile full`), README, GitHub Actions CI, uv.lock.
- [x] Phase 6 — `/v1/signals` + static dashboard (`/dashboard`) + `make backfill`.

Whole suite: **49 tests green**, ruff + mypy clean. Executable verification runs on SQLite
(aiosqlite); Postgres-specific execution (docker compose up, migrate on PG, ingest→PG) is
code-complete and awaits a Docker host. See PROGRESS.md.

## Key decisions
See `DECISIONS.md` for the full log. Headlines: package manager = **uv**; CLAUDE.md authored from
real data (no original existed); no Docker in build env so DB-dependent DoDs are written-and-
code-reviewed but executed against `aiosqlite` in tests; filing_id (`Comunicato n.`) is the
idempotency key; one transaction row per price/volume pair.
