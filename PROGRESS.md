# PROGRESS.md

## 2026-06-19 — autonomous build session (Phases 0–6 complete)

### Environment reality (affects what's verifiable)
- Host had only Python 3.9, no uv/poetry, **no Docker**. Installed **uv** + CPython 3.11.15.
- **No original CLAUDE.md / repo existed** → authored CLAUDE.md from **real** filings (D-001).
- Network works → downloaded real Allegato 3F filings for fixtures + a live ingest.
- No Docker → Postgres/Redis can't run here; DB/API code runs in tests against **aiosqlite**
  (DB-agnostic). See DECISIONS D-002. All compose/migration/Postgres code is code-complete.

### What was built and VERIFIED
- **Phase 0** scaffolding: uv project, docker-compose (pg15+redis7, healthchecks, volumes),
  Makefile, pre-commit, config. `ruff` + `mypy` clean.
- **Phase 1** DB: SQLAlchemy 2.x models (issuers/persons/filings/transactions), `signal_value`
  GENERATED STORED column, async engine, idempotent upsert, seed. Alembic initial migration
  **applies cleanly** (verified on SQLite; same migration targets Postgres).
- **Phase 2** parser (the core): section-anchored Allegato 3F extraction. **32 tests** — exact
  CEMBRE `0088-10-2026` ground truth + 6 cross-issuer fixtures (natural/legal person, buy/sell,
  multi price/volume, IT/EN) + garbage→failed. `signal_value` 92.5×950=87875 verified.
- **Phase 3** scrapers: polite async client (UA, ≥1 req/s, 429 backoff, robots.txt),
  eMarketStorage paginated listing + filter; 1Info stubbed (Vue SPA, no Selenium — D-007).
  **LIVE ingest verified on SQLite: 91 filings, 96.7% parse success, 2353 transactions, 31
  issuers; re-run ingested 0 (idempotent).**
- **Phase 4** API: `/health`, `/v1/transactions` (+all filters+meta), `/v1/transactions/{id}`
  (404), `/v1/issuers`, `/v1/persons`, `/v1/feed?since`, `/v1/signals`, `/docs`. Decimals as
  strings, ISO-8601 UTC, Redis token-bucket rate limit (graceful fallback). **13 API tests.**
  Smoke-tested live: `type=A&min_value=50000` → 57 rows; signals top = PIRELLI €616,500.
- **Phase 5** ops: APScheduler (15-min market hours / hourly otherwise, Europe/Rome), Dockerfile,
  combined compose (`--profile full`), README, GitHub Actions CI (ruff+format+mypy+pytest),
  uv.lock. **1 scheduler test.**
- **Phase 6** stretch: `/v1/signals` (C-suite open-market buys), static dashboard at
  `/dashboard`, `make backfill YEAR=...`.

**Whole suite: 49 tests green. ruff + mypy clean.** Tag: `v0.1.0`.

### Half-done / caveats
- **Postgres execution not run here** (no Docker). To confirm the Postgres path on a Docker host:
  `docker compose up -d && make migrate && make scrape` then hit `/docs`. Expected to work as-is
  (code is DB-agnostic; migration verified on SQLite; aiosqlite-vs-asyncpg differences are nil
  for the SQL used). This is the one DoD not executed in this environment.
- 2 of 91 live filings parsed `failed`, 1 `partial` (96.7% success). They are stored with
  `raw_text` (nothing dropped). PIAGGIO `0835-29-2026` is one failed case — a layout variant
  worth inspecting to push success higher.
- 1Info source is stubbed (SPA). Needs its JSON/XHR API reverse-engineered (no headless browser).

### Single most useful next task
Run the stack on a Docker host (`docker compose up -d && make migrate && make scrape`) to tick the
last Postgres-execution DoD; then inspect the 3 failed/partial fixtures (start with PIAGGIO
`0835-29-2026`) to raise parse success above 97%.
