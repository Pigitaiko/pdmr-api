# DECISIONS.md — engineering decision log

Append-only. Each entry: context → decision → reasoning. Newest first within a session.

## Session 2026-06-19 (autonomous build, Phase 0+)

### D-001 — No pre-existing CLAUDE.md; authored from real data
**Context:** The brief treats `CLAUDE.md` as the pre-existing source of truth (verified PDF schema,
DB design, Nexi `2170-25-2026` ground truth, role/market codes). No `CLAUDE.md` and no repo existed
anywhere in the environment. The referenced "Nexi 2170-25-2026" filing was not retrievable.
**Decision:** Author `CLAUDE.md` myself, grounding every "verified" claim in **real** Allegato 3F
PDFs downloaded live from eMarketStorage. Use the real **CEMBRE `0088-10-2026`** filing as the
canonical fixture instead of the missing Nexi one, plus 6 more real filings for layout coverage.
**Reasoning:** Operating principle says make the reasonable call and keep going rather than wait
(user asleep). Inventing a fake "verified" schema would violate faithful reporting; deriving it from
real filings yields a *genuinely* verified schema and real test ground truth.

### D-002 — No Docker in build environment
**Context:** `docker`/`docker compose` are not installed and cannot be installed autonomously
(Desktop requires an admin GUI install). Several DoDs ("docker compose up brings up Postgres+Redis
healthy", "migration applies to Docker Postgres", "ingest 50 filings into Postgres") require a
running Postgres/Redis.
**Decision:** Author all Docker/Postgres/Redis code correctly (`docker-compose.yml`, async asyncpg
engine, alembic migration, Redis-backed scraper state + rate limit) so it runs on any machine that
*has* Docker. For executable tests here, run the DB/API suite against **`aiosqlite`** (SQLAlchemy is
DB-agnostic; SQLite ≥3.31 supports the `signal_value` computed column). Clearly mark in PROGRESS.md
which DoDs are "code-complete, executed on SQLite" vs "executed on Postgres".
**Reasoning:** Maximises real, runnable verification without the one unavailable dependency; keeps
the Postgres path honest and ready for the user's machine. Switching tactic per "if an approach is
blocked, switch and log it."

### D-003 — Package manager = uv
**Decision:** Use `uv` (installed 0.11.22; pins CPython 3.11.15). **Reasoning:** Env had only Python
3.9 and no poetry; uv installs a modern interpreter + fast locking with one tool. Noted per brief.

### D-004 — User-Agent contact placeholder, real contact only in local .env
**Decision:** Committed default `USER_AGENT=PDMR-API-bot/0.1 (+contact@pdmr-api.example)` in
`.env.example`; the operator puts a real reachable address in gitignored `.env`.
**Reasoning:** "Respect the sources" wants a real contact, but "never commit secrets / personal data"
wins for committed files. Keeps personal email out of git history.

### D-005 — Filing identity = `Comunicato n.` (`NNNN-NN-YYYY`)
**Decision:** Idempotency/dedup key is the eMarketStorage "Comunicato n." id (e.g. `0088-10-2026`),
present on the cover page and repeated in the footer `Fine Comunicato n.<id>`.
**Reasoning:** Stable, unique per filing, machine-extractable, matches the `NNNN-NN-YYYY` format the
brief itself referenced. Redis SET also tracks seen *source URLs* as a cheap pre-filter.

### D-006 — One transaction row per price/volume pair
**Context:** A single `Operazione` block can list several `<price> EUR <volume>` fills for the same
instrument/day/venue.
**Decision:** Emit one `transactions` row per (price, volume) pair, sharing the operation's
instrument/nature/date/venue. `signal_value` = `price*volume` as a STORED generated column.
**Reasoning:** Makes `min_value`/signal filtering operate on real per-fill notional, keeps the
generated column a trivial expression, and loses no information.
