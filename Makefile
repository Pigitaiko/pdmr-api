.PHONY: up down test lint format migrate scrape serve seed backfill revision

up:
	docker compose up -d postgres redis

down:
	docker compose down

# Bring up the whole stack (api + scheduler too)
up-full:
	docker compose --profile full up -d

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run mypy .

format:
	uv run ruff format .
	uv run ruff check --fix .

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

seed:
	uv run python -m seed

scrape:
	uv run python -m scraper.ingest

serve:
	uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

backfill:
	uv run python -m scraper.ingest --backfill --year $(YEAR)
