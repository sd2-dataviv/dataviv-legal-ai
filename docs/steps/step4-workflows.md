# Step 4 — Workflows, approvals and playbooks

**Implements:** WFL-01..05, WFL-07, WFL-06 (definitions as JSON through the API) · **Status:** done, 10 October 2026 — 102/102 tests on Windows, mypy clean

A workflow is a named, versioned chain of typed steps. Each run reads the matter's documents, writes with citations, stops for a person's approval where needed, and saves the result as a draft. Every step records its inputs, output, model, tokens and cost.

## Built-in workflows (WFL-02)

| Workflow | Steps | Approval |
|---|---|---|
| Contract review against a playbook | playbook check → review memo → sign-off → save memo | matter lead or partner |
| Legal notice | find supporting facts → draft notice in Indian form → approval → save notice | matter lead or partner |
| Order or judgment brief | read court, case number, date, bench, parties, outcome, directions, next date → write brief → save | none |

## Step types (WFL-01)

| Type | Does | Output later steps can use |
|---|---|---|
| `retrieve` | keyword search in the matter (optionally chosen documents) | `passages`, `text` |
| `extract` | answers named fields from one document; every quote and figure checked (same code as review tables) | `fields`, `passages`, `text` |
| `review_table` | creates a review table in the matter and fills it, a few documents per slice | `rows`, `passages`, `text`, `table_id` |
| `playbook_review` | finds each playbook clause in the agreement, then classifies it: preferred, fallback, unacceptable or missing, with reasons and suggested wording | `clauses`, `counts`, `passages`, `text` |
| `llm` | writes (`draft`) or answers (`qa`) from earlier steps' passages, with `[S1]` citations and the sentence verifier | `sentences`, `sources`, `text` |
| `approval` | stops the run until an authorised person approves or rejects (with a reason) | `status`, `decided_by`, `comment` |
| `output_document` | saves a step's text as a Draft titled `document_title` with numbered sources; if an approval step showed that text and was approved, the draft is created already approved | `draft_id` |

`title` is the step's plain display name; other parameters use `{{inputs.x}}`, `{{steps.<id>.<field>}}`, `{{matter.title}}`, `{{matter.client_name}}` and `{{today}}`. A definition is validated before it is saved and again before every run; validation lists every problem at once. Input types: text, textarea, number (min/max), choice, document, documents, playbook. Inputs are checked before a run starts: documents must be in the matter, playbooks in the firm.

## Durable runs (WFL-03, WFL-05) — ADR-017

A run is a database row with one row per step. The worker's `workflow` job advances a run by one step (or one slice of a review table) and re-queues itself, so uploads and review tables are never stuck behind a long workflow.

1. Lock the run, pick the next unfinished step, mark it running (one transaction).
2. Run the step; model calls happen with no transaction open.
3. Lock the run, store the output, tokens and cost, mark the step done, re-queue (one transaction). A step's side effects (an approval request, the output draft) are written in this same transaction.

If the worker dies mid-step, the job is reclaimed after ten minutes and the step runs again; after three attempts it fails. **Retry** carries on from the failed step without repeating finished ones. A run keeps a snapshot of its definition and of the playbook it read, so editing either never changes a run already under way.

## Approvals (WFL-04)

| Required role | Who may decide |
|---|---|
| `matter_manager` | the matter lead or a partner on the matter |
| `firm_partner` | a firm partner who is on the matter |

The person who started the run can never approve it. Rejecting needs a comment; the run then stops and nothing is delivered. Cancelling a waiting run cancels its approval. `GET /v1/approvals?mine=true` is the approver's queue; the console shows it as "Workflows · N to approve".

## Playbooks (WFL-07)

Per firm. Each clause has a question to ask of the agreement and the preferred, fallback and unacceptable positions, plus guidance. Partners and admins create (from a sample — share purchase for the buyer, lease for the lessee — or from scratch), edit (each save is a new version) and delete. Everyone in the firm can read them. The dev seed adds both samples.

## Firm workflows (WFL-06, MVP)

Partners and admins publish definitions with `POST /v1/workflows` (`POST /v1/workflows/validate` checks one without saving). Re-publishing a slug adds a version; built-in slugs cannot be overwritten. A no-code builder is Phase 3.

## API

| Method & path | Purpose |
|---|---|
| GET /v1/workflows · GET /v1/workflows/{slug} | built-in and firm definitions |
| POST /v1/workflows/validate · POST /v1/workflows | check / publish a definition |
| POST /v1/matters/{id}/workflow-runs · GET same | start a run / list a matter's runs |
| GET /v1/workflow-runs/{id} · POST …/cancel · POST …/retry | run detail with every step; cancel; retry |
| GET /v1/approvals?mine= · POST /v1/approvals/{id}/decision | approval queue; approve or reject |
| GET /v1/playbook-templates · /v1/playbooks (CRUD) | playbooks |

## Console

**Workflows** tab: start a workflow (the form is built from its inputs), follow the run as a numbered timeline with each step's output — passages that open in the inspector, extracted details, the playbook table with colour-coded positions, cited text with verifier counts, the approval card, and a link to the saved draft — and manage playbooks.

## Try it

1. `bootstrap.bat`, then `frontend.bat`; sign in as **Rohan Gupta** (associate, member of every seeded matter).
2. Open *Share purchase agreement — Project Lotus*, upload an SPA, then Workflows → *Contract review against a playbook*, choose the agreement and the buyer playbook, Start.
3. When it shows *Waiting for approval*, switch to **Asha Sharma** (partner, matter lead) and approve. The memo appears under Drafts, already approved.

## Deferred

| Item | Why / when |
|---|---|
| Temporal | needs a server (Docker or virtualisation) and a compiled SDK that Smart App Control blocks; the engine is behind `run_workflow_job`, so it can move without API changes (ADR-017) |
| Branching, loops, parallel steps | not needed by the built-ins; add with the no-code builder |
| Approver chosen per run, approval deadlines and reminders | with notifications |
| Semantic retrieval in `retrieve` | Step 1c |
