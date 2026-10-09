# Dataviv AI OS

Legal AI operating system for Indian law firms: **Vault · Assistant · Workflows · Agents**, built on a
tenant-isolated, matter-permissioned, fully audited core.

| Doc | What it is |
|---|---|
| [docs/01-requirements.md](docs/01-requirements.md) | SRS: users, requirement IDs (FND/VLT/AST/WFL/AGT), NFRs, acceptance criteria |
| [docs/02-design.md](docs/02-design.md) | Architecture, data model, API contracts, sequences, test strategy, delivery steps |
| [docs/adr/](docs/adr/README.md) | Architecture decision records |

## Status

| Step | Module | State |
|---|---|---|
| 0 | Foundation: config, auth, tenancy (RLS), permissions, ethical walls, audit chain, matters API, CI, VS Code | **Done** |
| 1 | Vault: upload, ingestion pipeline with progress, OCR, chunking, hybrid search | Next |
| 2 | Assistant + frontend | — |
| 3 | Review tables + evaluation harness | — |
| 4 | Workflows (Temporal, approvals, playbooks) | — |
| 5 | Agents (tools, runtime loop, budgets, memory) | — |

## Prerequisites

Python 3.12+, Docker Desktop (or Docker Engine), VS Code, Git. `make` is optional — every command is also a VS Code task.

## Quick start (VS Code)

1. Open the `dataviv-ai-os` folder in VS Code and install the recommended extensions when prompted.
2. Terminal → in the repo root:
   ```bash
   cd backend
   python3.12 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   cp .env.example .env
   cd ..
   ```
   Then `Ctrl+Shift+P → Python: Select Interpreter → backend/.venv`.
3. `Ctrl+Shift+P → Tasks: Run Task`, in order:
   - **1 · Start database** (Postgres 16 in Docker; creates `app_user` and `dataviv_test`)
   - **2 · Migrate database**
   - **3 · Seed dev data** (prints a token per user)
4. Press **F5** → "API: FastAPI (debug, reload)". Open http://localhost:8000/docs, click **Authorize**, paste the partner token.
5. Run tests: `Ctrl+Shift+P → Tasks: Run Test Task`, or the Testing sidebar.

With `make`: `make setup up migrate seed api` and `make check`.

## How isolation works

- The API connects as `app_user`, which is **not** the schema owner, so PostgreSQL row-level security always applies.
- Each request runs in one transaction with `app.firm_id` set transaction-locally; policies filter every table by it.
- Matter access = membership minus ethical walls, decided only in `app/core/permissions.py`.
- Inaccessible matters return **404**, and the denial is written to the audit chain.
- Audit events are hash-chained per firm; `app_user` cannot UPDATE or DELETE them.

The test suite proves each of these against a real PostgreSQL (`tests/test_rls.py`, `tests/test_matters.py`, `tests/test_audit.py`).

## Development process

Branch `feat/<module>-<req-id>` → PR (template checklist) → CI green (ruff, mypy, pytest) → review → merge to `main`.
