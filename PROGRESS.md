# PROGRESS.md

## 2026-06-19 — autonomous build session

### Environment reality (affects what's verifiable)
- Python 3.9 only on host → installed **uv** + CPython 3.11.15.
- **No Docker** in env → Postgres/Redis can't run here. DB/API code is written correctly for
  Postgres and executed in tests against `aiosqlite`. See DECISIONS D-002.
- Network works → downloaded **7 real Allegato 3F filings** as fixtures (CEMBRE, CALTAGIRONE,
  ITALGAS, EMAK, INTESA SANPAOLO, SANLORENZO, TINEXTA).
- **No original CLAUDE.md existed** → authored it from the real filings (DECISIONS D-001).

### Phase 0 — Scaffolding ✅ (committed)
- uv project (`pyproject.toml`, py3.11), full dir structure, `.gitignore`, `.env.example`.
- `docker-compose.yml` (postgres:15 + redis:7 healthchecks + named volumes; api/scheduler under
  `full` profile). `Makefile` (up/down/test/lint/format/migrate/scrape/serve/seed/backfill).
- `.pre-commit-config.yaml` (ruff + mypy). `config.py` settings.
- **Verified:** `ruff check`, `ruff format`, `mypy` all green. (Docker compose authored, not run —
  no Docker here.)

### Half-done / next
- Next task: **Phase 2 PDF parser** (highest value, fully verifiable against the 7 real fixtures),
  then Phase 1 models/migration, then API.

### Single most useful next task
Build `scraper/parser.py` + `tests/test_parser.py` asserting the CEMBRE ground truth in CLAUDE.md.
