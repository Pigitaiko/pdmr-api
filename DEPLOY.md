# Deploying PDMR to the public web (Render)

This repo ships a **`render.yaml` blueprint** that provisions the whole stack — API + landing
page and PostgreSQL — and serves it on a public HTTPS URL. The free tier is enough to share a
live, interactive demo. (Redis is intentionally omitted; the app rate-limits in-process and
dedupes at the DB layer without it.)

## One-time deploy (≈5 minutes, all in the browser)

1. Go to **<https://render.com>** and sign up (free — use "Sign in with GitHub" so Render can read
   the repo).
2. In the Render dashboard: **New → Blueprint**.
3. Pick the **`Pigitaiko/pdmr-api`** repository. Render detects `render.yaml` and shows the
   services it will create: `pdmr-db` (Postgres) and `pdmr-api` (web).
4. Click **Apply**. Render builds the Docker image, runs the database migration, and starts the
   service. First build takes a few minutes.
5. When `pdmr-api` goes **Live**, click its URL — it looks like
   **`https://pdmr-api.onrender.com`**. That's your public, shareable link.

On first boot the app runs **one background scrape** and populates real filings, so within a
minute or two of going live the landing page and `/v1/*` endpoints serve live data. Visit:

- `https://<your-app>.onrender.com/` — the landing page
- `https://<your-app>.onrender.com/docs` — interactive API docs
- `https://<your-app>.onrender.com/dashboard` — the dashboard
- `https://<your-app>.onrender.com/v1/signals` — JSON

## Good to know (free tier)

- **Cold starts:** a free web service sleeps after ~15 min of no traffic; the next visit takes
  ~30–50 s to wake. Fine for sharing; upgrade the `pdmr-api` plan to `starter` for always-on.
- **Free Postgres expires** after ~30 days. To keep the demo alive, upgrade the database, or
  re-apply the blueprint to recreate it (data resets, then re-bootstraps).
- **Data refresh:** the free deploy scrapes once on first boot. To keep it continuously updated,
  add an always-on scraper: in the Render dashboard create a **Background Worker** from this repo
  with start command `uv run python -m scraper.scheduler` and the same `DATABASE_URL` /
  `REDIS_URL` env vars (Background Workers are a paid instance type).
- **Coverage:** on first boot the scrape runs every source (`source="all"`) — Italy, Sweden,
  Netherlands, France, Belgium, the Nasdaq Nordic/Baltic bloc (FI/DK/IS/EE/LV/LT) and Norway —
  so the live demo shows all 12 markets.

## Other hosts

The stack is plain Docker + Postgres + Redis, so Railway, Fly.io, or any VPS work too. The app
auto-normalizes a `postgres://` / `postgresql://` `DATABASE_URL` to the async driver, so you only
need to set `DATABASE_URL` (and optionally `REDIS_URL`), run `alembic upgrade head`, and start
`uvicorn api.main:app`.
