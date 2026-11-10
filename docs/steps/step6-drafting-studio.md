# Step 6 — Drafting studio (add-on)

Write a contract, legal notice, reply, affidavit, power of attorney, opinion or letter **from the lawyer's own
facts**, with no matter and no uploaded documents. Documents are kept in a firm-wide history and downloaded as
Word. They are never linked to a matter.

It is a self-contained add-on. Nothing in the Assistant, Drafts, Matters or Vault modules changed. What the add-on
touches outside its own files:

| File | Change |
|---|---|
| `backend/app/main.py` | 1 import + 1 `include_router` line |
| `frontend/src/App.tsx` | "Matters / Drafting studio" switch in the top bar (view kept in `localStorage`) |
| `frontend/src/api.ts` | `send` and `saveFrom` exported (two words), so the studio client shares the request log and token |

To remove it, revert those lines and run `alembic downgrade 0011`.

## Document types (`backend/app/modules/studio/templates.py`)
| Category | Templates |
|---|---|
| Contracts | NDA, service agreement, employment, consultancy, leave and licence, MoU, partnership deed, loan |
| Notices | Recovery of money, cheque bounce (s. 138 NI Act), general legal notice, reply to a notice |
| Affidavits and authorities | Affidavit, power of attorney |
| Advice and letters | Legal opinion, letter or e-mail |
| Any other document | Free description (deed, resolution, undertaking, bond…) |

Each template has fields (the facts it asks for, some required) and a **brief**: drafting guidance sent to the
model that stays on the server. To add a type, append a `Template(...)` to `TEMPLATES`. There is no migration and no
frontend change: the console builds the form from `GET /v1/studio/templates`.

## How a document is written (`prompts.py`, `service.py`)
- The prompt holds the brief, the facts, the instructions and **today's date in IST** (used for deadlines such as
  the s. 138 30-day and 15-day periods). There are no sources: the model is told never to invent names, dates,
  amounts or events, and to write `[placeholders]` for anything missing.
- The rules also cover:
  - Indian law, including BNS/BNSS/BSA for criminal matters after 1 July 2024;
  - no case law unless the lawyer supplies it;
  - plain text with a formal heading and numbered clauses;
  - a closing `DRAFTING NOTES` list: assumptions, placeholders, deadlines, and stamp paper where the document
    needs it.
- The output is cleaned before it is stored:
  - stray Markdown is stripped;
  - "1. 1.1" numbering becomes "1.1";
  - trailing spaces are removed.
- Guards:
  - **Loops.** If the model repeats the same substantial line four times, the stream is closed (which also
    stops the request to the model server), the repeats are cut, and the document carries a warning.
  - **Length limit.** Reaching `DATAVIV_STUDIO_MAX_OUTPUT_TOKENS` (default 6000) also carries a warning.
- **Revise** writes a new version from the current text, which may include the lawyer's own edits, plus the
  requested change. Earlier versions are kept, and all versions of one document share a `family_id`.

## API (`/v1/studio`, any role except `client`)
| Method | Path | |
|---|---|---|
| GET | `/templates` | Catalogue with fields |
| POST | `/documents` | Write a new document. SSE: `meta`, `notice*`, `delta*`, then `done` or `error` |
| POST | `/documents/{id}/revise` | New version with the requested change (SSE) |
| GET | `/documents?q=&template=&mine=&limit=` | Firm history, one row per document (latest version). `q` searches title, text and facts |
| GET | `/documents/{id}` | Text, facts, tokens, latency and the list of versions |
| PATCH | `/documents/{id}` | Rename, or save hand edits (author, partner or admin) |
| DELETE | `/documents/{id}` | Soft-deletes the document and all its versions (author, partner or admin) |
| GET | `/documents/{id}/export` | Word: centred heading, bold headings, indented sub-clauses, yellow placeholders, drafting notes, review footer |

Validation errors (unknown template or field, required facts missing with no instructions) come back as
problem+json **before** streaming starts.

## Data (`migrations/sql/0012_studio.sql`)
`studio_documents` is firm-scoped with RLS (`tenant_isolation`). It holds `root_id`/`parent_id` (versions),
`template`, `title`, `inputs` (jsonb), `instructions`, `content`, `status` (streaming / complete / failed), and
`error` (the failure, or a warning on a complete document). It also records model, tokens, latency and cost,
`deleted_at`, and a `tsv` search column over title, text and the facts. `app_user` has SELECT, INSERT and UPDATE
only: there are no hard deletes.

Audit events:
- `studio.generate` / `studio.revise`: the request (field names only, no text).
- `ai.call`: model, prompt SHA-256, tokens, latency and cost; `feature: studio`.
- `studio.update`, `studio.export`, `studio.delete`.

## Verified
- Tests: `tests/test_studio_api.py` (11). They run offline against PostgreSQL and cover:
  - streaming and storage;
  - prompt contents;
  - validation;
  - client-role refusal;
  - versions and history search;
  - isolation from other firms;
  - edit, export and delete permissions;
  - model failure;
  - the loop guard and the length warning;
  - output clean-up.
- Full suite: 179 passed, 1 skipped. ruff, mypy and `tsc` are clean.
- Live on `gpt-oss:20b` (Ollama, 192.168.0.84):

  | Document | Time | Tokens | Result |
  |---|---|---|---|
  | s. 138 notice | 10–12 s | ~650 | Customary form; deadlines worked out (pay by 25 Nov; send by 3 Dec) |
  | Mutual NDA | 29 s | ~1,600 | Arbitration and execution blocks |
  | NDA revision | 30 s | | Made one-way; 12-month non-solicitation added |
  | Name-change affidavit | 5 s | | Stamp-paper note |

  Word export opened and checked.

## Known limits
- A local 20B model writes a sound first draft but is not a lawyer. Wording, statutes and facts must be reviewed,
  and the Word footer says so. Larger models, or Groq `gpt-oss-120b`, write longer and more careful documents.
- One Ollama server handles one request at a time, so two people drafting at once queue behind each other.
- A revision changes only what is asked: it does not re-check the rest of the earlier version.
