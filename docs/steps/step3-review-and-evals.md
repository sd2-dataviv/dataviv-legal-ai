# Step 3 — Review tables and the evaluation harness

**Implements:** VLT-09, VLT-10 (MVP scale), NFR-03, NFR-09 · **Status:** done, 10 October 2026 — 89/89 tests on Windows; benchmark PASS with Groq (18/18 assistant cases, 15/15 review cells, zero unflagged fabrications, total cost $0.0045)

## Review tables

Each row is a document and each column a question. Every cell holds a short answer, a normalised value, the exact supporting quote, the page, and whether that quote and its figures were verified against the document.

| In this step | Deferred (and why) |
|---|---|
| Templates: contract review, litigation file, notices; or your own columns | Firm-defined template library → with firm settings |
| Answer types: text, date (ISO), amount (₹, lakh/crore aware), yes/no, choice | Lists and multi-value cells |
| One model call per document for all its columns; strict JSON output | Semantic retrieval within long documents → Step 1c |
| Background job with live progress; cells fill in as documents are read; cancel any time | |
| Quote must appear word for word in the cited passage and every figure must match, or the cell says "check" | |
| People can correct a cell (the model's answer is kept) and mark it reviewed; re-runs never overwrite a correction | |
| Changing a column's question clears that column (old answers answered something else) | |
| Export: Excel (answers + an Evidence sheet with quotes, pages, checks, who corrected or reviewed) and CSV (UTF-8, opens Hindi correctly in Excel) | |

**ADR-015 — One call per document, sliced jobs.** A 40-document by 8-column table is 40 model calls, not 320, which matters on Groq's 8K tokens-per-minute tier. Short documents are sent whole; long ones send the opening passage plus the two best passages per question. The job reads five documents, then re-queues itself, and the worker always looks for new uploads first, so a long review never blocks ingestion. Rate limits pause and retry (up to six attempts, honouring `retry-after`).

Permissions follow the matter: viewers read and export; lead, partner and member roles create, run and correct. Walled users, admins who are not members, and other firms get 404.

## Evaluation harness (NFR-03)

`python -m evals.run` (Windows: `scripts\windows\evals.bat`) rebuilds the **test** database, uploads the corpus in `backend/evals/corpus` (six fictional Indian documents: share purchase agreement, legal notice with a prompt-injection line, bail order, lease deed, employment agreement, Hindi rent notice), runs every suite through the real API, and writes `logs/evals/eval-<time>.md` and `.json`. It exits 1 when a gate fails, so it can gate a release.

| Suite | Measures | Gate |
|---|---|---|
| assistant (18 cases) | correct facts; citation to the right document | ≥ 0.8 each |
| | abstains when the documents do not say | ≥ 0.66 |
| | ignores instructions hidden in a document | 1.0 |
| | **unflagged fabrications** — a forbidden (wrong) fact in a sentence the verifier did not flag | **0** |
| review (3 documents × 5 columns) | normalised values correct; quotes verified | ≥ 0.8 each |

**ADR-016 — A small, fixed, adversarial benchmark gates releases.** It is cheap enough to run on every release (about 25 model calls), deterministic in what it checks, and aimed at the failures that matter most to a law firm: wrong figures presented as sourced, obeying text inside documents, and answering when the documents are silent. Gates are in the suite files; tighten them as the system improves.
