# Step 1 — Vault (ingestion, chunking, search)

**Implements:** VLT-01..06, VLT-07, VLT-08 (keyword part), VLT-11, NFR-09 · **Status:** in build, 9 October 2026

## Scope

| In this step | Deferred (and why) |
|---|---|
| Upload one or many files into a matter (PDF, DOCX, TXT/MD, EML, PNG/JPG/TIFF) | DOC/RTF conversion (needs LibreOffice) → Step 1b |
| SHA-256 dedupe per matter; same filename = new version | E-mail attachments as child documents → Step 1b |
| Text extraction: PDF text layer, DOCX, e-mail body, plain text | Docling layout parsing (heavy ML deps; blocked by Smart App Control risk) → when on Linux/GPU |
| OCR for scans/images through the Dataviv OCR service (HTTP adapter) | Built-in OCR engine — the platform calls the existing service |
| Rule-based legal document classification | Model-based classification via the gateway → Step 2 |
| Structure-aware chunking (clauses, numbered paras, sections) with page + character spans | — |
| Keyword search (PostgreSQL full-text, English stemming + language-neutral tokens for Hindi), permission-filtered, with highlighted snippets | Semantic/dense search with Qdrant + bge-m3 → Step 1c (needs Qdrant service) |
| Background ingestion with per-file progress on `GET /v1/jobs/{id}` | Temporal (Step 4); the job table is the same contract |
| Soft delete (removed from search immediately), download original, citation lookup | Retention purge job → Phase 4 |

## Decisions (ADR-008 … ADR-010)

- **ADR-008 — PostgreSQL full-text search first.** Runs on every deployment with nothing extra to install, sits under the same RLS policies, and is already hybrid-ready: Step 1c adds dense vectors and fuses rankings. The `SearchIndex` seam is the chunks table + one query function.
- **ADR-009 — Database job queue before Temporal.** Jobs are rows; workers claim them with `claim_next_job()` (`FOR UPDATE SKIP LOCKED`, a `SECURITY DEFINER` function so workers never need to bypass RLS). Stale `running` jobs are reclaimed after 10 minutes, so a crash loses no work. Temporal replaces the claim loop in Step 4 without changing the `jobs` contract.
- **ADR-010 — Storage adapter, local disk in dev.** Originals are content-addressed (`firm/matter/aa/<sha256>`) and immutable. An S3/MinIO/Azure Blob adapter implements the same three methods.

## Ingestion pipeline (per file)

1. **Store** original (content-addressed), create `document` + `document_version`.
2. **Extract** pages: PDF text layer per page; pages with < 25 characters are marked `needs_ocr`.
3. **OCR** `needs_ocr` pages if `DATAVIV_OCR_URL` is set; otherwise the document is `partial` and lists the pages awaiting OCR.
4. **Classify**: header/keyword rules → petition, written statement, order, judgment, agreement, notice, affidavit, correspondence, other.
5. **Chunk**: split on legal structure (numbered clauses `1.`, `1.1`, `(a)`, `Section 4`, `ARTICLE`, `CLAUSE`, headings in capitals), pack to ≤ 1,200 characters, keep `section_path`, pages and character spans.
6. **Index**: chunk rows carry a generated `tsvector`; GIN index.

## OCR service contract (adapter expects)

`POST {DATAVIV_OCR_URL}/ocr` multipart `file` (+ optional `languages=eng+hin`) → `200 {"pages": [{"page": 1, "text": "...", "confidence": 0.93}]}`. If the Dataviv OCR service differs, only `app/modules/vault/ocr.py` changes.

## API

| Method & path | Purpose |
|---|---|
| `POST /v1/matters/{id}/documents` (multipart `files`) | Upload; returns `job_id` + per-file status (`queued`, `new_version`, `duplicate`) |
| `GET /v1/matters/{id}/documents` | List (filter `doc_type`, `status`) |
| `GET /v1/documents/{id}` | Detail + versions |
| `GET /v1/documents/{id}/download` | Original file (audited) |
| `GET /v1/documents/{id}/chunks/{chunk_id}` | Citation target: text, pages, span |
| `DELETE /v1/documents/{id}` | Soft delete; chunks removed from search |
| `POST /v1/search` | `{query, matter_ids?, doc_types?, limit}` → ranked chunks with snippets |

## Acceptance (Step 1)

- Upload of mixed files reports per-file progress and ends `succeeded`.
- Re-uploading identical bytes is reported `duplicate`; same filename with new bytes becomes version 2 and replaces the indexed text.
- Search finds the right document for a member; returns **nothing** for a non-member, a walled user, or another firm.
- Deleted documents disappear from search immediately.
- Every new table has RLS (enforced by the existing test).
