# 02 — Technical Design

**Implements:** `01-requirements.md` v0.1
**Status:** Draft v0.1 — 9 October 2026

---

## 1. Architecture

```
┌──────────────────────── Frontend (Next.js) ─────────────────────────┐
│ Matter workspace · Vault browser · Review table · Assistant · Runs  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTPS (REST + SSE streaming)
┌───────────────────────────────▼─────────────────────────────────────┐
│ Core API — FastAPI (modular monolith)                               │
│  core/  auth · tenancy · permissions · audit · config               │
│  modules/ matters · vault · assistant · workflows · agents          │
└──────┬──────────────┬───────────────┬──────────────┬────────────────┘
       │              │               │              │
  PostgreSQL 16    Qdrant         MinIO / Blob    Temporal ──► Workers
  (RLS tenancy)   (vectors+ACL)   (originals)     (durable)     ingest · review · workflow · agent
                                                                 │
                                                       Model gateway (LiteLLM)
                                                       frontier APIs │ vLLM │ TEI embeddings
```

One deployable API image and one worker image. Modules talk through Python interfaces, not HTTP, until scale demands a split (ADR-001).

## 2. Repository layout

```
dataviv-ai-os/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory
│   │   ├── core/                # config, security, tenancy, permissions, audit, logging
│   │   ├── db/                  # SQLAlchemy base, session, RLS helpers
│   │   ├── api/                 # router aggregation, dependencies
│   │   └── modules/
│   │       ├── matters/         # models, schemas, service, router
│   │       ├── vault/           # documents, ingestion pipeline, search, review tables
│   │       ├── assistant/       # threads, retrieval, generation, verifier
│   │       ├── workflows/       # definitions, runs, steps, playbooks
│   │       └── agents/          # agent defs, runtime, tools, memory
│   ├── migrations/              # Alembic
│   ├── tests/                   # pytest; mirrors modules
│   └── pyproject.toml
├── frontend/                    # Next.js app (Step 2 onwards)
├── infra/                       # docker-compose, Helm later
├── docs/                        # SRS, design, ADRs, runbooks
├── scripts/                     # dev helpers (seed, benchmark)
├── .vscode/                     # launch, tasks, settings, extensions
└── .github/workflows/           # CI
```

Each module has the same internal shape: `models.py` (tables) · `schemas.py` (Pydantic I/O) · `service.py` (business logic, no HTTP) · `router.py` (HTTP only) · `tasks.py` (background jobs, where needed).

## 3. Cross-cutting design

### 3.1 Tenancy and permissions (FND-01, 03, 04)

- Every table has `firm_id UUID NOT NULL`.
- Postgres **row-level security** on every tenant table: `USING (firm_id = current_setting('app.firm_id')::uuid)`. The API sets `SET LOCAL app.firm_id` at the start of each transaction from the authenticated principal. The app also filters by `firm_id` (defence in depth).
- Matter access: `matter_members(matter_id, user_id, role)`; `ethical_walls(user_id, matter_id)` = hard deny. Single function `permissions.accessible_matter_ids(principal)` is the only source of truth, used by API, retrieval and agents. (OpenFGA replaces its internals later without changing callers — ADR-004.)
- The app connects as a non-owner role (`app_user`) so RLS cannot be bypassed.

### 3.2 Audit (FND-05)

`audit_events(id, firm_id, actor_id, action, object_type, object_id, matter_id, detail JSONB, prev_hash, hash, created_at)` — insert-only; `hash = sha256(prev_hash || row)` per firm, giving a tamper-evident chain. Revoke UPDATE/DELETE from `app_user`.

### 3.3 Model gateway (FND-06)

`core/llm.py` exposes `complete()`, `stream()`, `embed()`, `rerank()` with a **task class** (`extract | qa | draft | reason`). It reads firm model policy and calls LiteLLM. In tests and offline dev, a deterministic `FakeLLM` is used — no network.

### 3.4 Background jobs and progress (NFR-09)

All long jobs run as Temporal workflows and write progress to a `jobs` table (`status`, `progress 0–100`, `current_step`, `total`, `done`, `error`). The UI polls `/jobs/{id}` or subscribes via SSE. CLI scripts show `tqdm` progress bars.

## 4. Data model (MVP tables)

| Table | Key columns | Module |
|---|---|---|
| firms | id, name, deployment_mode, model_policy JSONB | core |
| users | id, firm_id, email, name, role | core |
| matters | id, firm_id, client_name, title, matter_type, forum, cnr, status | matters |
| matter_members | matter_id, user_id, firm_id, matter_role | matters |
| ethical_walls | id, firm_id, user_id, matter_id, reason | matters |
| documents | id, firm_id, matter_id, title, doc_type, source, current_version_id, status, deleted_at | vault |
| document_versions | id, firm_id, document_id, storage_key, sha256, mime, pages, created_by | vault |
| chunks | id, firm_id, matter_id, version_id, ordinal, section_path, page_from, page_to, char_start, char_end, text, ocr_confidence | vault |
| review_tables / review_columns / review_cells | table→columns (question, type); cell (document, column, answer, source chunk, status) | vault |
| threads / messages | thread per matter; message role, content, citations JSONB, verifier JSONB, model, cost | assistant |
| workflow_defs / workflow_runs / workflow_steps | def (slug, version, spec JSONB); run (status, temporal_id); step (I/O, model, cost) | workflows |
| playbooks | id, firm_id, name, clauses JSONB | workflows |
| agent_defs / agent_runs / agent_steps / matter_memory | def (instructions, tools, budgets); run trace; memory facts | agents |
| approvals | id, firm_id, subject_type, subject_id, required_role, decision, decided_by, comment | workflows/agents |
| jobs | id, firm_id, kind, status, progress, current_step, total, done, error | core |
| audit_events | see 3.2 | core |

Qdrant collection `chunks` per deployment, point payload: `firm_id, matter_id, document_id, version_id, doc_type, chunk_id`. Every query carries a mandatory filter `firm_id = X AND matter_id IN accessible`.

## 5. API contracts (v1)

| Method & path | Purpose | Req |
|---|---|---|
| GET /health | liveness/readiness | — |
| GET /v1/me | current principal | FND-02 |
| POST /v1/matters · GET /v1/matters · GET /v1/matters/{id} | matters (list returns only accessible) | FND-03 |
| POST /v1/matters/{id}/members | add member (partner/admin) | FND-03 |
| POST /v1/matters/{id}/documents | multipart upload, returns job id | VLT-01 |
| GET /v1/matters/{id}/documents · GET /v1/documents/{id} | list / detail | VLT |
| GET /v1/documents/{id}/chunks/{chunk_id} | citation target (text + page + span) | AST-03 |
| POST /v1/search | hybrid search with filters | VLT-08 |
| POST /v1/review-tables · POST …/{id}/run · GET …/{id} | review table | VLT-09 |
| POST /v1/threads · POST /v1/threads/{id}/messages (SSE) | Assistant | AST-01..06 |
| GET /v1/workflows · POST /v1/workflows/{slug}/runs · GET /v1/workflow-runs/{id} | workflows | WFL |
| POST /v1/approvals/{id}/decision | approve / reject | WFL-04, AGT-03 |
| POST /v1/agents/{id}/runs · GET /v1/agent-runs/{id} | agents | AGT |
| GET /v1/jobs/{id} | progress | NFR-09 |

Errors follow RFC 9457 problem+json. A matter the user cannot access returns **404, not 403**, so existence is not leaked.

## 6. Key sequences

### 6.1 Ingest (VLT-03)

1. `POST documents` → store original in object storage, insert `document` + `version` (status `queued`), create `job`, start Temporal `IngestDocument`.
2. Worker: convert (LibreOffice) → parse (Docling) → OCR low-text pages (Dataviv OCR / Surya for Hindi) → classify (small model + header rules) → structure-aware chunk → embed (bge-m3 dense+sparse) → upsert Qdrant with ACL payload → status `indexed`. Each stage updates `job.progress`.
3. Failures retry 3× with backoff; final failure marks the document `failed` with reason.

### 6.2 Ask (AST-01..04)

1. Resolve `accessible_matter_ids` → plan query (task class `extract`) → hybrid search with mandatory filter → rerank → top-k chunks with IDs.
2. Generate (task class `qa`/`draft`) with instruction: cite `[c:<chunk_id>]` per sentence; stream to client.
3. Verifier (task class `extract`) checks each sentence vs its cited chunk → labels; stored on the message.
4. Audit event with prompt hash, model, chunk IDs, verifier summary, cost.

### 6.3 Workflow with approval (WFL-03, 04)

Temporal workflow executes steps from the spec; an `approval` step creates an `approvals` row and waits on a signal. `POST /approvals/{id}/decision` sends the signal. The run survives worker restarts.

### 6.4 Agent loop (AGT-02..04)

`while steps < budget and cost < budget`: model proposes tool call(s) → runtime validates arguments against the Pydantic schema → permission check as starting user → read tools execute; write tools create an approval and pause → observation appended → repeat until `final_answer`. Every step persisted to `agent_steps`.

## 7. Testing strategy

| Level | Tooling | Gate |
|---|---|---|
| Unit | pytest, FakeLLM | every commit |
| Integration | pytest against real Postgres 16 (RLS on) | every commit |
| Permission leak suite | pytest adversarial cases | every commit, zero tolerance |
| Legal benchmark | eval harness (Step 3+) | release |
| E2E | Playwright (Step 2+) | release |

## 8. SDLC process

1. **Requirements** (this SRS) → 2. **Design** (this doc + ADRs) → 3. **Implement** in feature branches `feat/<module>-<req-id>` → 4. **Test** (CI must pass: ruff, mypy, pytest) → 5. **Review** (PR, one reviewer, checklist in PR template) → 6. **Deploy** (staging on merge to `main`, production on tag) → 7. **Maintain** (benchmark regression weekly, incident runbooks).

Delivery steps for this build:

| Step | Delivers | Requirements |
|---|---|---|
| 0 | Scaffold: config, tenancy, RLS, permissions, audit, matters API, CI, VS Code | FND-01..05 |
| 1 | Vault: upload, ingestion pipeline with progress, chunking, search | VLT-01..08, 11 |
| 2 | Assistant: threads, retrieval, streamed cited answers, verifier; frontend | AST-01..04, 06, 10 |
| 3 | Vault review tables + eval harness | VLT-09, NFR-03 |
| 4 | Workflows: spec engine, Temporal runs, approvals, 3 built-ins | WFL-01..05, 07 |
| 5 | Agents: tool registry, runtime loop, budgets, approvals, memory | AGT-01..07 |

## 9. Architecture decision records

See `docs/adr/`.
