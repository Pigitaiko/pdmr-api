#!/usr/bin/env sh
# Container start: run DB migrations (retrying until Postgres is reachable, so a cold
# free-tier database can't abort the boot), then serve. Kept in a file rather than an
# inline dockerCommand because Render mis-parses complex inline shell (exit 127).
set -e

until uv run alembic upgrade head; do
  echo "db not ready, retrying in 3s..."
  sleep 3
done

exec uv run uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-10000}"
