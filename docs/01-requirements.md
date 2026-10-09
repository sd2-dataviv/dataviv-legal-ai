# 01 — Software Requirements Specification (SRS)

**Product:** Dataviv AI OS — core AI modules
**Scope of this SRS:** Vault, Assistant, Workflows, Agents (backlog tasks 07–10, 15–16, 27–29, 38–42, 52, 56–57, 66–69, 73, 78–85)
**Jurisdiction:** India first
**Status:** Draft v0.1 — 9 October 2026

---

## 1. Purpose

Build the four modules that make Dataviv AI OS a Harvey-class legal AI platform:

| Module | One-line definition | Harvey equivalent |
|---|---|---|
| **Vault** | Secure, matter-scoped document store with ingestion, OCR, chunking, indexing and bulk extraction | Vault + Review Table |
| **Assistant** | Chat interface that answers, summarises and drafts, grounded in permitted sources with verified citations | Assistant |
| **Workflows** | Versioned, repeatable multi-step legal procedures (e.g. contract review against a playbook) with human approval steps | Workflows |
| **Agents** | Goal-driven runtime that plans, calls typed tools, keeps matter memory and pauses for approval | Agent Builder / long-horizon agents |

Build order follows dependency: **Vault → Assistant → Workflows → Agents**. Each depends on the one before.

## 2. Users and roles

| Role | Can | Cannot |
|---|---|---|
| Partner | Everything on matters they belong to; approve workflow/agent actions; manage matter membership | See walled matters |
| Associate | Read/write matter documents; run Assistant, workflows, agents | Approve partner-gated actions |
| Junior / Paralegal | Upload, read, run Assistant and pre-approved workflows | Run agents with write tools; approve |
| Admin | Firm configuration, users, model policy, knowledge sources | Read matter content unless also a member |
| Client (later phase) | Shared documents via portal | Everything else |

## 3. Functional requirements

Priority: **M** = must (MVP), **S** = should, **C** = could.

### 3.1 Platform foundation (FND)

| ID | Requirement | Pri |
|---|---|---|
| FND-01 | Every record belongs to exactly one firm (tenant); no API call can read another firm's data | M |
| FND-02 | Users authenticate via OIDC (Keycloak); dev mode uses signed local tokens | M |
| FND-03 | Matters have members with a matter role; access to any matter-scoped object requires membership | M |
| FND-04 | Ethical walls deny access regardless of membership | M |
| FND-05 | Every create/update/delete and every AI call writes an append-only audit event | M |
| FND-06 | All model calls go through one gateway with per-firm model policy | M |

### 3.2 Vault (VLT)

| ID | Requirement | Pri |
|---|---|---|
| VLT-01 | Upload PDF, DOCX, DOC, TXT, EML/MSG, PNG/JPG/TIFF into a matter (single and bulk, up to 500 files per batch) | M |
| VLT-02 | Store original immutably with SHA-256 hash; duplicate detection within a matter | M |
| VLT-03 | Ingestion pipeline: convert → parse → OCR (where needed) → classify → chunk → embed → index, as a background job with visible progress per file | M |
| VLT-04 | OCR handles English and Hindi; per-page confidence stored; low-confidence pages flagged | M |
| VLT-05 | Chunks follow document structure (section/clause/paragraph) and keep page + character span | M |
| VLT-06 | Classify document type (petition, written statement, order, judgment, agreement, notice, affidavit, correspondence, other) | M |
| VLT-07 | Document versions: new upload of same document creates a version, never overwrites | S |
| VLT-08 | Hybrid search (keyword + semantic) within permitted matters, with filters (type, date, matter) | M |
| VLT-09 | **Review table**: user defines columns as questions; system extracts an answer + source span per document per column; runs as a background job; exportable to CSV/XLSX | M |
| VLT-10 | Review table scales to 1,000 documents in MVP (target 10,000 in Phase 4) | S |
| VLT-11 | Delete document (soft delete, retained per firm retention policy, removed from index immediately) | M |

### 3.3 Assistant (AST)

| ID | Requirement | Pri |
|---|---|---|
| AST-01 | Threaded chat scoped to a matter (or firm knowledge), answers streamed token-by-token | M |
| AST-02 | User chooses sources per question: this matter's Vault, selected documents, firm knowledge, Indian case law, statutes | M |
| AST-03 | Every factual sentence carries a citation to a source span; clicking opens the document at that span | M |
| AST-04 | Verifier labels each sentence supported / partially / unsupported; unsupported shown highlighted | M |
| AST-05 | Case and statute citations resolved against the legal index; unresolved ones flagged | S (Phase 2 data) |
| AST-06 | Modes: Ask, Summarise, Draft (notice, reply, email, memo) | M |
| AST-07 | Structured questions ("deadlines in next 7 days") answered from the database, not RAG | S |
| AST-08 | Model selector: Auto (routed by task) or a specific permitted model | S |
| AST-09 | Export answer/draft to DOCX | S |
| AST-10 | Thread history searchable; threads belong to matter and inherit its permissions | M |

### 3.4 Workflows (WFL)

| ID | Requirement | Pri |
|---|---|---|
| WFL-01 | Workflow definition = versioned, declarative spec (YAML/JSON) of typed steps: input form, retrieve, extract, LLM step, review-table step, human approval, output document | M |
| WFL-02 | Ship 3 built-in workflows: (a) contract review against a playbook, (b) legal notice drafting, (c) order/judgment brief | M |
| WFL-03 | Runs are durable (survive restart), resumable, and show step-by-step progress | M |
| WFL-04 | Human approval step pauses the run until an authorised role approves/rejects with comment | M |
| WFL-05 | Each step's inputs, outputs, model, sources and cost recorded on the run | M |
| WFL-06 | Firm admins and legal engineers create/edit workflows (no-code builder in Phase 3; YAML in MVP) | S |
| WFL-07 | Playbooks (clause positions: preferred / fallback / unacceptable) stored per firm and reused by workflows | M |

### 3.5 Agents (AGT)

| ID | Requirement | Pri |
|---|---|---|
| AGT-01 | Agent = goal + system instructions + allowed tools + model policy + approval policy | M |
| AGT-02 | Runtime loop: plan → call tools → observe → revise, with a step budget and cost budget | M |
| AGT-03 | Tools are typed (Pydantic) functions; read tools run freely, write tools require approval per policy | M |
| AGT-04 | Agent acts with the starting user's permissions, never more; cannot widen scope mid-run | M |
| AGT-05 | Matter memory: structured facts and prior outputs stored in Postgres, permission-checked | S |
| AGT-06 | Orchestrator–subagent pattern for large jobs (parallel subagents over document sections or documents) | S |
| AGT-07 | Full trace of every step visible to the user and stored in audit | M |
| AGT-08 | Agent Builder: users compose agents from tools and instructions and share within firm | C (Phase 3) |

## 4. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-01 | Security | Zero cross-tenant or cross-matter leaks; enforced by Postgres RLS and retrieval filters; tested in CI |
| NFR-02 | Privacy | No client data used for model training; zero-retention model endpoints; India-region hosting by default |
| NFR-03 | Accuracy | No unflagged fabricated citation reaches the user; benchmark gates every release |
| NFR-04 | Latency | Assistant first token ≤ 3 s (p95) for matter Q&A; search ≤ 800 ms (p95) |
| NFR-05 | Throughput | Ingestion ≥ 60 pages/min per worker on CPU OCR path; scalable horizontally |
| NFR-06 | Availability | 99.5% (SaaS, MVP) |
| NFR-07 | Portability | Same code runs cloud, on-premise, air-gapped; no managed-only dependencies |
| NFR-08 | Observability | Every request traceable end-to-end (OpenTelemetry + Langfuse) |
| NFR-09 | UX feedback | Every long-running job (ingestion, review table, workflow, agent) reports progress (% and current step) |
| NFR-10 | Compliance | Audit logs retained ≥ 1 year (DPDP Rules minimum); breach-report export available |
| NFR-11 | Code quality | Type-checked, linted, ≥ 80% coverage on core modules, every PR reviewed |

## 5. Acceptance criteria (MVP exit, per module)

- **Vault:** upload 100 mixed files (incl. 20 scanned, 5 Hindi) → all indexed with progress shown; search returns the right document in top 5 for ≥ 90% of 50 test queries; a user outside the matter gets zero results.
- **Assistant:** on the 150-question document Q&A suite, ≥ 85% rubric pass, 100% of answers cite sources, 0 unflagged unsupported sentences on the hallucination suite.
- **Workflows:** contract-review workflow runs end-to-end on 10 agreements, pauses for partner approval, resumes after restart, and produces a DOCX report with clause-level citations.
- **Agents:** a "prepare matter brief" agent completes using ≥ 3 tools, respects step/cost budget, and its write action (create tasks) waits for approval.

## 6. Out of scope (this SRS)

Billing, client portal, email connector, calendar, analytics, Office add-ins, air-gapped packaging. These follow in later phases of the master blueprint.

## 7. Assumptions and dependencies

- Design-partner firms supply anonymised documents and benchmark questions.
- Indian case-law corpus from AWS Open Data (SC, 25 HCs; CC-BY-4.0) is loaded in Phase 2.
- Frontier model access via India-region endpoints; open-weight models on vLLM for private deployments.
- Existing Dataviv assets reused: Pydantic tool kit, OCR service, Azure 2× A10 VM.

## 8. Traceability

Each requirement ID appears in: design doc section → code module → test file name (`test_<id>_*.py` or test docstring). Task-sheet IDs are listed in the module headers of `02-design.md`.
