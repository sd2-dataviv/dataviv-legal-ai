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
| 1 | Vault: upload, background ingestion with progress, OCR adapter, classification, clause-aware chunking, keyword search ([details](docs/steps/step1-vault.md)) | **Done** — 47 tests green on Windows |
| 1c | Vault: semantic search (Qdrant + bge-m3), fused with keyword ranking; used by Search, Assistant, Agents and Workflows ([details](docs/steps/step1c-semantic-search.md)) | **Done** — 129 tests green on Linux (semantic on and off); live bge-m3 + Qdrant verified |
| 1t | Test console: React app that exercises every backend endpoint as any seeded user ([frontend/](frontend/README.md)) | **Built** |
| 2 | Assistant: matter threads, streamed answers with citations, sentence verifier, Ask / Summarise / Draft, Groq gateway with firm model policy, Word export ([details](docs/steps/step2-assistant.md)) | **Done** — 72 tests green on Windows, Groq live |
| 2b | Drafts: edit, version history and comparison, conflict-safe saves, partner approval, comments, presence, Word export ([details](docs/steps/step2b-drafts.md)) | **Done** — 80 tests green on Windows |
| 2c | Live drafts: several people type into one draft at once (Yjs), AI suggestions as shared redlines that an editor accepts, approval locks exactly what everyone sees ([details](docs/steps/step2c-live-drafts.md)) | **Done**: 140 tests green on Linux; live two-person run with Groq verified |
| 2d | Containers: one image for API, worker and console; Compose stack with Postgres 16 and Qdrant ([details](docs/docker.md)) | **Done**: smoke test green in Docker (ingest, hybrid search, live drafts with Groq, approval) |
| 2e | E-signature: send an approved draft to DocuSign, signers in order, live status, signed PDF and certificate kept in the Vault ([details](docs/steps/step2e-esignature.md)) | **Done**: 148 tests green on Linux; DocuSign client tested against a mock DocuSign server; test mode verified in the console |
| 3 | Review tables (templates, cited and verified cells, corrections, Excel/CSV export) + evaluation harness gating releases ([details](docs/steps/step3-review-and-evals.md)) | **Done** — 89 tests green; benchmark 18/18 + 15/15 on Groq |
| 4 | Workflows: durable runs, 3 built-ins (contract review against a playbook, legal notice, order brief), approvals, firm playbooks, versioned definitions ([details](docs/steps/step4-workflows.md)) | **Done** — 102 tests green on Windows |
| 5 | Agents: typed tools, step loop with budgets, approvals, matter memory, subagents, full trace; 4 built-in agents ([details](docs/steps/step5-agents.md)) | **Done** — 115 tests green on Windows; live Groq run verified |
| 6 | Drafting studio (add-on): contracts, legal notices, replies, affidavits, powers of attorney, opinions and letters from the lawyer's own facts, no matter needed; firm-wide history, versions, Word export ([details](docs/steps/step6-drafting-studio.md)) | **Done**: 179 tests green on Windows; live on Ollama gpt-oss:20b |

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
4. Press **F5** → "API: FastAPI (debug, reload)". Open http://127.0.0.1:8010/docs, **Authorize**, paste the partner token.
5. Tests: `Ctrl+Shift+P → Tasks: Run Test Task`, or the Testing sidebar.

`scripts\windows\check_requirements.bat` re-checks the machine at any time (read-only).

## Quick start — Linux without Docker (Ubuntu 24.04, native PostgreSQL 16)

```bash
bash scripts/linux/bootstrap.sh --semantic   # setup + database (asks for sudo once) + lint/types/tests + Groq check
bash scripts/linux/frontend.sh               # npm install + typecheck + build the console
bash scripts/linux/console.sh                # starts Qdrant + API, opens http://127.0.0.1:8010/ui
```

Also in `scripts/linux/`: `setup.sh` and `setup_db.sh` (the first two bootstrap steps), `qdrant.sh` (start/stop/status),
`evals.sh` (benchmark; `--backend fake` for an offline smoke run), `agent_report.sh`, `check_llm.sh`. Logs go to `logs/`.

Semantic search is on when `backend/.env` has `DATAVIV_EMBEDDINGS_BACKEND=local` and `DATAVIV_VECTOR_STORE=qdrant`.
The first upload downloads bge-m3 (2.2 GB) once. For documents uploaded before that, run `.venv/bin/python scripts/reindex_vectors.py`.

## Quick start — everything in Docker

```bash
bash scripts/linux/docker.sh up      # builds the image; starts postgres, qdrant and the api (+ worker)
# console: http://localhost:8080/ui/   API docs: http://localhost:8080/docs
bash scripts/linux/docker.sh smoke   # end-to-end check: upload, hybrid search, live drafts + AI, approval
```

Settings come from `backend/.env` (Groq key, embeddings); nothing secret is baked into the image. Details,
options and production notes: [docs/docker.md](docs/docker.md).

## Quick start — macOS / Linux (only Postgres in Docker)

```bash
make setup up      # .venv + deps; Postgres 16 in Docker (creates app_user and dataviv_test)
make migrate seed  # schema + dev data and tokens
make api           # http://127.0.0.1:8010/docs
make check         # lint + types + tests
```

## Test console (React)

A browser console for the backend: switch between seeded users, open matters, upload files and watch ingestion,
search with cited passages, add members and raise ethical walls, and see every request with its status and timing.

- **Windows:** run `scripts\windows\frontend.bat` once (npm install + build), then `scripts\windows\console.bat`.
  It starts the API and opens http://127.0.0.1:8010/ui.
- **Live-reload while editing the console:** start the API (F5), then `cd frontend && npm run dev` → http://localhost:5173.

Things worth trying: open a matter as Asha, then switch to Kavya (other firm) — the console keeps the matter selected
and the API answers 404. Raise a wall on Rohan as IT Admin, then switch to Rohan.

## Try the Assistant

1. Put your Groq key in `backend\.env` as `GROQ_API_KEY=...` and run `scripts\windows\bootstrap.bat`. It switches the gateway to Groq and makes one tiny test call (see `logs\05_check_llm.log`).
2. `scripts\windows\frontend.bat`, then `scripts\windows\console.bat`. Open a matter that has indexed documents and use the **Assistant** tab.
3. Ask, Summarise or Draft. Click a numbered citation to open the passage; underlined sentences did not fully match their source.

## Run the benchmark

`scripts\windows\evals.bat` asks 18 benchmark questions and fills a review table using your Groq key, on the **test** database (your dev data is untouched). Takes about 5-10 minutes on Groq's developer tier; the report is written to `logs\evals\`. Offline smoke run: `scripts\windows\evals.bat -Backend fake`.

## Try the Vault (Swagger)

1. F5 to start the API (the background worker starts with it), open http://127.0.0.1:8010/docs, **Authorize** with the partner token.
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
