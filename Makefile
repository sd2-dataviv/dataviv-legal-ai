.PHONY: setup up up-ai down migrate seed api test lint fmt typecheck check

PY := backend/.venv/bin/python
BIN := backend/.venv/bin

setup:            ## create venv and install backend + dev deps
	cd backend && python3.12 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"
	cp -n backend/.env.example backend/.env || true

up:               ## start postgres
	docker compose -f infra/docker-compose.yml up -d postgres

up-ai:            ## start postgres + qdrant + minio + redis (Step 1+)
	docker compose -f infra/docker-compose.yml --profile ai up -d

down:
	docker compose -f infra/docker-compose.yml down

migrate:          ## apply database migrations (as schema owner)
	cd backend && .venv/bin/alembic upgrade head

seed:             ## sample firm, users, matters + dev tokens
	$(PY) scripts/seed_dev.py

api:              ## run the API with auto-reload on :8000
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

test:             ## run the test suite against dataviv_test
	cd backend && .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .

fmt:
	cd backend && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

typecheck:
	cd backend && .venv/bin/mypy app

check: lint typecheck test   ## everything CI runs
