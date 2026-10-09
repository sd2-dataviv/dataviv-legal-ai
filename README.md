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
| 1 | Vault: upload, background ingestion with progress, OCR adapter, classification, clause-aware chunking, keyword search ([details](docs/steps/step1-vault.md)) | **In test** |
| 1c | Vault: semantic search (Qdrant + bge-m3), fused with keyword ranking | — |
| 2 | Assistant + frontend | — |
| 3 | Review tables + evaluation harness | — |
| 4 | Workflows (Temporal, approvals, playbooks) | — |
| 5 | Agents (tools, runtime loop, budgets, memory) | — |

## Prerequisites

Python 3.12+ (3.14 tested on Windows), Git, VS Code, and PostgreSQL 16 — either native or via Docker.
`make` is optional; every command is also a VS Code task.

## Quick start — Windows (no Docker)

1. Install PostgreSQL 16 and set a password for the `postgres` user when the installer asks:
   ```powershell
   winget install PostgreSQL.PostgreSQL.16 --interactive
   ```
2. Open this folder in VS Code. `Ctrl+Shift+P → Tasks: Run Task`, in order:
   - **0 · Windows: first-time setup** — creates `.venv`, installs dependencies, creates `backend\.env`, installs extensions, first git commit
   - **1 · Database: start / prepare** — asks for the `postgres` password once, creates roles and databases, migrates, seeds, prints dev tokens
3. `Ctrl+Shift+P → Python: Select Interpreter` → `.venv` (VS Code usually picks it automatically).
4. Press **F5** → "API: FastAPI (debug, reload)". Open http://localhost:8000/docs, **Authorize**, paste the partner token.
5. Tests: `Ctrl+Shift+P → Tasks: Run Test Task`, or the Testing sidebar.

`scripts\windows\check_requirements.bat` re-checks the machine at any time (read-only).

## Quick start — macOS / Linux (Docker)

```bash
make setup up      # .venv + deps; Postgres 16 in Docker (creates app_user and dataviv_test)
make migrate seed  # schema + dev data and tokens
make api           # http://localhost:8000/docs
make check         # lint + types + tests
```

## Try the Vault

1. F5 to start the API (the background worker starts with it), open http://localhost:8000/docs, **Authorize** with the partner token.
2. `GET /v1/matters` → copy a matter id → `POST /v1/matters/{id}/documents` and choose files.
3. Poll `GET /v1/jobs/{job_id}` until `succeeded`, then `POST /v1/search` with `{"query": "termination notice"}`.

## How isolation works

- The API connects as `app_user`, which is **not** the schema owner, so PostgreSQL row-level security always applies.
- Each request runs in one transaction with `app.firm_id` set transaction-locally; policies filter every table by it.
- Matter access = membership minus ethical walls, decided only in `app/core/permissions.py`.
- Inaccessible matters return **404**, and the denial is written to the audit chain.
- Audit events are hash-chained per firm; `app_user` cannot UPDATE or DELETE them.

The test suite proves each of these against a real PostgreSQL (`tests/test_rls.py`, `tests/test_matters.py`, `tests/test_audit.py`).

## Development process

Branch `feat/<module>-<req-id>` → PR (template checklist) → CI green (ruff, mypy, pytest) → review → merge to `main`.
