# Step 1c — Semantic search

**Implements:** VLT-08 (hybrid keyword + semantic search) · **Decision:** ADR-019 (extends ADR-003, ADR-008) · **Status:** done, 15 October 2026 — 129/129 tests on Linux with semantic search on (incl. a real Qdrant round trip), 128 + 1 skipped with it off; ruff and mypy clean. Live on Ubuntu, CPU only: bge-m3 + Qdrant 1.19.2, about 115 ms per search, 3 files ingested and embedded in 10 s (`logs/23_semantic_livecheck.log`).

**Live examples:** *"Can the buyer walk away from the deal?"* gets 0 keyword hits; hybrid puts **12. TERMINATION** first, and the Assistant (Groq) answers with 12.1 and 12.2 cited, for $0.00014. A Hindi question about the notice period finds the English termination clause (similarity 0.65). An English question about release conditions finds the Hindi bail order. *"What is the capital of France?"* returns nothing, and so does a search by another firm's partner.

Search now finds passages by meaning as well as by words. "Can the buyer walk away from the deal?" finds
*"Either party may terminate this Agreement by giving thirty days written notice"*, though they share no
words. Hindi questions find English passages and the other way round, because bge-m3 is multilingual.
The change is in one place, so everything that retrieves passages gets it: `/v1/search`, the Assistant
(Ask and Draft), the agents' `search_documents` tool and the workflow `retrieve` step.

## How it works

```
query ─┬─► PostgreSQL full-text (english + simple), top 40 ──────────────┐
       │                                                                 ├─► reciprocal rank fusion ─► top N
       └─► embed (bge-m3) ─► Qdrant, filter firm + accessible matters ───┘        │
              top 40 with cosine ≥ 0.5                                            ▼
                                                     hits found only by meaning are loaded from
                                                     Postgres under RLS with the same matter scope
```

- **Ingestion** gains an *embed* stage after chunking. Passages are committed first, so keyword search works
  at once. Then they are embedded in batches, with progress on the job (*"2/5 notice.pdf: understanding meaning
  32/120 passages"*), and stored in Qdrant with `firm_id, matter_id, document_id, version_id, doc_type`.
  `chunks.embedding_model` records which model embedded each passage.
- **Fusion:** reciprocal rank fusion (k = 60) of the two rankings. A passage in both rises to the top. Each hit
  says how it matched: `keyword`, `semantic` or `both`, plus its cosine `similarity` when found by meaning.
- **Similarity floor:** a passage found *only* by meaning must have cosine ≥ `DATAVIV_SEMANTIC_MIN_SCORE`
  (0.5, set for bge-m3). Nearest neighbours are always "something", so without a floor an unanswerable question
  would always get passages and the Assistant could not abstain.
- **Modes:** `POST /v1/search` takes `mode`: `hybrid` (default), `keyword` or `semantic`. The response says
  which mode actually ran, plus a `notice` when it fell back.

## Isolation (NFR-01)

A vector store is a second copy of the data with its own filter, so it gets the same paranoia as Postgres:

1. `Scope` cannot be built without a firm and at least one accessible matter (from `accessible_matter_ids()`).
   The store turns it into a mandatory filter, so no caller can forget it.
2. Every passage found by meaning is loaded from Postgres as `app_user` under RLS, with the same matter
   scope and `deleted_at IS NULL`. A stale, walled, deleted or foreign vector is dropped there.
3. Walls and membership changes take effect immediately. Nothing is re-indexed, because the filter is applied
   at query time.

`tests/test_semantic_search.py` repeats every keyword leak case on the semantic path: non-member, other firm,
admin, wall after membership, matter and type filters, and a vector whose delete was missed.

## Backends

| Setting | Values | Notes |
|---|---|---|
| `DATAVIV_EMBEDDINGS_BACKEND` | `none` (default), `local`, `http`, `fake` | `local` = sentence-transformers in-process (Linux/macOS); `http` = TEI or any OpenAI-compatible `/embeddings` (production: TEI + bge-m3 on the GPU VM); `fake` = tests |
| `DATAVIV_VECTOR_STORE` | `none` (default), `qdrant`, `memory` | Qdrant over REST with httpx; `memory` = tests |
| `DATAVIV_EMBEDDINGS_MODEL` / `_DIM` | `BAAI/bge-m3` / `1024` | one Qdrant collection per model, e.g. `dataviv_chunks_baai_bge_m3_1024` |
| `DATAVIV_SEMANTIC_MIN_SCORE` | `0.5` | floor for meaning-only matches |

With either backend `none`, everything behaves exactly as in Step 1. If the embedder or Qdrant is down,
search returns keyword results with a notice, and ingestion still succeeds. Passages left unembedded are
listed by `GET /v1/search/status` and picked up by `scripts/reindex_vectors.py`.

## Operations

| Task | Command |
|---|---|
| Start Qdrant (no Docker) | `bash scripts/linux/qdrant.sh` (stop / status) |
| Embed passages uploaded before semantic search was on, or while it was down | `.venv/bin/python scripts/reindex_vectors.py` (`--status` to look only) |
| Switch model | set `DATAVIV_EMBEDDINGS_MODEL` / `_DIM`, then `reindex_vectors.py`: a new collection is created and the old one is untouched |
| Check from the API | `GET /v1/search/status`: enabled, model, reachable, vectors, pending passages |

## API

| Method & path | Change |
|---|---|
| POST /v1/search | new `mode`; hits gain `match` and `similarity`; response gains `mode` and `notice` |
| GET /v1/search/status | new |
| GET /v1/documents/{id} | new `semantic_chunks` (null when semantic search is off) |

## Console

The **Search** tab has *Words + meaning / Exact words / Meaning only*, a status line (model, passages indexed,
pending), a chip on each hit (*words*, *meaning*, *words + meaning*) and its similarity.

## Deferred

| Item | Why / when |
|---|---|
| bge-m3 sparse vectors | Postgres full-text already covers the lexical side; revisit if recall on rare terms needs it |
| Reranker (bge-reranker-v2-m3) | after the 150-question suite shows where fusion ranks wrongly |
| Review tables' per-document passage choice by meaning | they still pick passages by keywords (ADR-015); small change once tuned |
| Pruning vectors whose passage vanished while Qdrant was down | harmless (dropped at query time); add `--prune` when it matters for storage |
