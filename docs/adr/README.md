# Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| 001 | Modular monolith (one API, one worker image) before microservices | Accepted |
| 002 | PostgreSQL row-level security for tenancy, plus app-level filters | Accepted |
| 003 | Qdrant for vectors (dense + sparse) with mandatory ACL payload filters | Accepted |
| 004 | Single `accessible_matter_ids()` permission function; OpenFGA later behind it | Accepted |
| 005 | Temporal for durable jobs, workflows and agent runs | Accepted |
| 006 | LiteLLM gateway with task classes; FakeLLM in tests | Amended by 012 |
| 007 | Inaccessible matters return 404, not 403 | Accepted |
| 008 | PostgreSQL full-text search first; dense vectors added in Step 1c | Accepted |
| 009 | Database job queue (`claim_next_job`, SKIP LOCKED) before Temporal | Accepted |
| 010 | Storage adapter; content-addressed local disk in dev | Accepted |
| 011 | Pure-Python dependencies where possible (Windows Smart App Control blocks unsigned native modules) | Accepted |
| 012 | In-process gateway over OpenAI-compatible HTTP (Groq first); LiteLLM later as one more backend | Accepted |
| 013 | Deterministic lexical answer verifier by default; model verifier optional and can only make labels stricter | Accepted |
| 014 | Drafts: versioned saves with conflict detection and presence, not live co-editing (yet) | Accepted |
| 015 | Review tables: one model call per document, quotes verified, jobs re-queue in slices behind uploads | Accepted |
| 016 | A small adversarial benchmark (`evals/`) gates releases; zero unflagged fabrications | Accepted |
| 017 | Durable workflow runs in Postgres, not Temporal (for now) | Accepted |
| 018 | Agents decide in one strict-JSON step at a time | Accepted |
| 019 | Hybrid search: Postgres full-text + Qdrant dense (bge-m3), fused by reciprocal rank; vector hits re-checked under RLS | Accepted |
| 020 | Live co-editing (Yjs/pycrdt) over the version model; AI edits are suggestions a person accepts; approval locks the shared copy | Accepted (amends 014) |

## ADR-002 — PostgreSQL RLS for tenancy

**Context.** A single missed `WHERE firm_id = …` in application code would leak one firm's data to another.
**Decision.** Enable RLS on every tenant table; the API sets `app.firm_id` per transaction and connects as a non-owner role. Application queries still filter by `firm_id`.
**Consequences.** Leaks need two independent bugs. Migrations must add a policy to every new tenant table (enforced by a test that inspects `pg_class.relrowsecurity`).

## ADR-007 — 404 for inaccessible matters

**Context.** A 403 confirms a matter exists, which can itself reveal a confidential client relationship.
**Decision.** Any matter-scoped object the principal cannot access returns 404.
**Consequences.** Slightly harder debugging; audit log records the denied attempt.

## ADR-012 — In-process model gateway

**Context.** ADR-006 planned a LiteLLM proxy. The first provider, Groq, speaks the OpenAI chat-completions protocol, and the dev machine is Windows without Docker, where an extra service and compiled dependencies are costly (ADR-011).
**Decision.** `core/llm.py` holds the gateway: task classes, per-firm model policy (`firms.model_policy`), cost accounting, and backends behind one `Backend` protocol (`FakeLLM`, `GroqBackend` over `httpx`). All model calls go through `Gateway`.
**Consequences.** No extra service; rate limits and errors are handled in one place. Adding a provider means one backend class; LiteLLM can become one when there are several.

## ADR-013 — Lexical verifier by default

**Context.** AST-04 needs every sentence labelled. A model-based check doubles token use, and Groq's developer tier allows about 8K tokens a minute.
**Decision.** A deterministic verifier checks figures (amounts, dates, clause numbers) exactly and term coverage against the cited passage. An optional model pass (`DATAVIV_ASSISTANT_VERIFIER=model`, strict JSON schema) can downgrade labels, never upgrade them.
**Consequences.** Free, fast and repeatable; paraphrases with different wording may be marked *partly supported*, which errs on the side of caution.

## ADR-014 — Versioned drafts before live co-editing

**Context.** Firms want drafts that several people work on and a partner approves. Live co-typing needs a CRDT sync server whose Python implementations are compiled extensions blocked by Smart App Control (ADR-011).
**Decision.** Every save is a new immutable version naming its base; a stale base gets 409 with who saved since, and the editor compares, then saves on top or reloads. Presence heartbeats show who is viewing or editing. Approval records the exact version approved.
**Consequences.** Nothing is ever lost or silently overwritten, and every change is attributable. Two people typing at once must reconcile on save; a CRDT layer can be added later without changing the version model.

## ADR-015 — Review tables: one call per document

**Context.** VLT-09 asks many questions of many documents. One call per cell multiplies cost and hits Groq's 8K tokens/minute limit quickly; a long job would also block uploads on the single dev worker.
**Decision.** One strict-JSON call per document answers all its columns. Each answer must quote a numbered passage; the quote and figures are checked before the cell counts as verified. Jobs process five documents, re-queue, and yield to ingestion.
**Consequences.** Cheap and fair to other work; a document's answers share one context, so very long documents rely on per-question retrieval of the best passages.

## ADR-016 — Benchmark gates releases

**Context.** NFR-03: no unflagged fabricated citation may reach the user, and a benchmark gates every release.
**Decision.** `backend/evals` holds a fixed corpus, JSON suites with expected and forbidden answers, pure scoring functions and a runner that uses the real API on the test database. Gates live in the suite files; the runner exits non-zero on failure.
**Consequences.** Regressions in grounding, abstention or injection resistance show up before release. The corpus is small; it grows with real (anonymised) failure cases.

## ADR-017 — Durable workflow runs in Postgres, not Temporal (for now)

**Context.** WFL-03 needs runs that survive restarts and pause for approval. The design named Temporal, which needs a server (Docker or WSL virtualisation, both off on the dev PC) and a Python SDK built on a compiled Rust core that Smart App Control blocks (ADR-011).
**Decision.** Runs and steps are rows. The existing job queue (`claim_next_job`, `FOR UPDATE SKIP LOCKED`, stale reclaim) advances a run one step per job; each step's result, cost and side effects are committed in one transaction with its status. Approval steps end the job; the decision starts a new one. Runs snapshot their definition and playbook.
**Consequences.** No new infrastructure, and runs are inspectable with SQL. At-least-once execution: a step interrupted mid-way runs again, so step handlers write only in the final transaction. No timers, signals or parallel branches yet; Temporal can replace `run_workflow_job` later without changing the API or tables.

## ADR-018 — Agents decide in one strict-JSON step at a time

**Context.** AGT-02 needs a tool-using loop on Groq's `openai/gpt-oss-120b`, with Pydantic-typed tools (AGT-03), approval pauses, and an offline model for tests. Native tool calling is a second, provider-specific code path; the strict JSON-schema path is already proven on Groq for review tables (ADR-015).
**Decision.** Each step, the model returns `{thought, tool, arguments}` under a strict schema whose `tool` is an enum of the agent's allowed tools; `arguments` is a JSON string validated by the tool's Pydantic model (extra fields forbidden). One tool per step; a step is a job on the existing queue (ADR-017), so approvals and subagents pause and resume durably. Passages are addressed by run-scoped ids (S1…), so the model never handles database ids.
**Consequences.** Provider-agnostic, testable with a deterministic fake, and every step is a clean trace row. Slightly more steps than parallel tool calls; invalid arguments cost a step but are fed back so the model can correct itself. Native tool calling can replace the decision format later without changing tools, tables or the API.

## ADR-019 — Hybrid search: full-text + dense vectors, fused, re-checked under RLS

**Context.** VLT-08 needs search by meaning as well as words. Lawyers paraphrase ("walk away" for termination), and matters mix Hindi and English. ADR-003 chose Qdrant and ADR-008 postponed vectors to Step 1c. Constraints: dev machines without Docker or a GPU, Windows Smart App Control (ADR-011), and a vector store that is a second copy of client data with its own filter.
**Decision.** Keep PostgreSQL full-text as the lexical ranking and add a dense ranking: bge-m3 vectors in Qdrant, one collection per model. Fuse the two by reciprocal rank (k = 60). The model is reached through a small embedding gateway (`core/embeddings.py`: `local` sentence-transformers, `http` TEI/OpenAI-compatible, `fake` for tests). Qdrant is reached over REST with httpx, not qdrant-client (grpcio and numpy are compiled). Every vector query takes a `Scope` (firm + accessible matters, never empty), which becomes a mandatory payload filter, `firm_id` being Qdrant's tenant index. Every hit is then loaded from Postgres under RLS with the same scope. Meaning-only hits must clear a cosine floor (0.5), so unanswerable questions still retrieve nothing. Ingestion embeds after committing the text, and failures leave passages pending (`chunks.embedding_model IS NULL`) for `scripts/reindex_vectors.py`. Search falls back to keywords with a notice.
**Consequences.** A leak through search needs the Qdrant filter *and* RLS *and* the SQL scope to fail. Walls apply instantly, with no re-indexing. Semantic search is optional per deployment and degrades to Step 1 behaviour. Swapping models is a config change plus a reindex. bge-m3 sparse vectors and a reranker are deferred. Embedding calls are not individually audited: `vault.search` records the mode and model, ingestion is audited by upload.

## ADR-020 — Live co-editing over the version model; AI edits as suggestions

**Context.** Lawyers in one firm want to work on the same draft at the same moment, use the AI Assistant inside it, and send the result to the matter lead for approval. ADR-014 deferred live co-typing because the CRDT libraries are compiled and Windows Smart App Control blocks them. Development has since moved to Linux, where they install normally.
**Decision.** Each draft gets a shared working copy, a Yjs document held in a server room (pycrdt) and synced with browsers over the standard y-websocket protocol on `/v1/drafts/{id}/live`. The full state is persisted in `draft_live` within a second of each change. Versions remain the immutable record: *Save version* (or asking for approval) snapshots the working copy and credits its contributors. The server enforces read-only for viewers and for everyone while a draft awaits approval. It refuses and disconnects rather than trusting the browser. Access is re-checked every 15 seconds, and the token travels in the WebSocket sub-protocol, not the URL. Events after REST changes go out only once the transaction commits (`after_commit` on `tenant_session`). The AI never writes into the draft: it produces a stored suggestion (quote, replacement, sources, warnings, cost) that an editor accepts or rejects. Accepting re-finds the quoted text and goes stale if the text changed. Server-side edits convert code-point offsets to pycrdt's UTF-8 byte offsets, so Hindi is safe.
**Consequences.** Simultaneous editing without lost work, and every change still ends up in an attributed, approvable version. The model's output is always reviewed by a person and audited (`ai.call`, `draft.suggestion*`). One API process owns the rooms, so horizontal scaling needs sticky routing per draft or a broker between processes. pycrdt is a compiled dependency; where it cannot load, live editing switches off and drafts work as in Step 2b.

## ADR-021 — E-signature through a provider interface; DocuSign over REST with the JWT grant

**Context.** Approved drafts must be signed by partners, clients and other parties, and the signed copy must stay with the matter. The firm uses DocuSign. Signing has to work unattended (no browser OAuth per user) and be testable offline. Under Indian law, a standard e-signature is an electronic record, while Aadhaar eSign or a DSC is an IT Act electronic signature, and some documents cannot be signed electronically at all.
**Decision.** A small `ESignBackend` interface (`core/esign.py`: create, status, void, download) with two backends. `DocuSign` calls the eSignature REST API v2.1 with httpx, authenticated by the JWT grant (an RS256 assertion for one consenting DocuSign user, scope `signature impersonation`; `cryptography` is imported only for this). `FakeESign` is an offline test mode whose state lives in files so the API and a separate worker agree. Only a draft's approved version is sent, as the same Word export people download, plus a signature block whose white anchor text places DocuSign's fields. Envelopes and signers are firm-scoped rows under RLS, with at most one open envelope per draft (a partial unique index). Status arrives by Connect callback (HMAC-verified), a worker sweep, or on request. Callbacks and the sweep find the firm through `SECURITY DEFINER` functions that return only ids and firms. On completion, the signed PDF and the certificate are stored as Vault documents (`source = 'esign'`) and ingested. The sender must confirm the document is not excluded by the IT Act's First Schedule.
**Consequences.** Signing needs no new infrastructure beyond DocuSign keys, and the whole flow is tested without the network. Signed copies are searchable and immutable like any upload, and every step is audited. Aadhaar eSign (DocuSign's eMudhra integration, enabled by DocuSign) and other providers fit behind the same interface. Polling every 60 seconds is the fallback when Connect cannot reach the server, so status can lag by up to a minute.
