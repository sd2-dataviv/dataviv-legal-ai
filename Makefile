.PHONY: setup up up-ai down migrate seed api test lint fmt typecheck check

PY := .venv/bin/python
BIN := $(abspath .venv/bin)

setup:            ## create venv and install backend + dev deps
	python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e "./backend[dev]"
	cp -n backend/.env.example backend/.env || true

up:               ## start postgres
	docker compose -f infra/docker-compose.yml up -d postgres

up-ai:            ## start postgres + qdrant + minio + redis (Step 1+)
	docker compose -f infra/docker-compose.yml --profile ai up -d

down:
	docker compose -f infra/docker-compose.yml down

migrate:          ## apply database migrations (as schema owner)
	cd backend && $(BIN)/alembic upgrade head

seed:             ## sample firm, users, matters + dev tokens
	$(PY) scripts/seed_dev.py

api:              ## run the API with auto-reload on :8000
	cd backend && $(BIN)/uvicorn app.main:app --reload --port 8000

test:             ## run the test suite against dataviv_test
	cd backend && $(BIN)/pytest

lint:
	cd backend && $(BIN)/ruff check . && $(BIN)/ruff format --check .

fmt:
	cd backend && $(BIN)/ruff check --fix . && $(BIN)/ruff format .

typecheck:
	cd backend && $(BIN)/mypy app

check: lint typecheck test   ## everything CI runs
