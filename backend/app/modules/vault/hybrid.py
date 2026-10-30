"""Semantic leg of hybrid search (VLT-08, ADR-019): index passages as vectors, find them by meaning,
and fuse that ranking with PostgreSQL full-text ranking.

Rules every caller relies on:
- A vector search is always scoped (``Scope``: firm + accessible matters, optionally documents and
  types). Callers then load the passages from Postgres under RLS with the same matter filter, so a
  vector hit for a deleted, walled or foreign passage is dropped before anyone sees it.
- Passages found only by meaning must clear DATAVIV_SEMANTIC_MIN_SCORE; nothing is returned just
  because it is the nearest of a bad lot.
- If the embedder or the store fails, search falls back to keyword results with a notice instead of
  failing; ingestion leaves the passages for ``backfill`` (scripts/reindex_vectors.py).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import Text, bindparam, func, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.embeddings import Embedder, EmbeddingError, embed_batched, get_embedder
from app.core.vectors import Hit, Point, Scope, VectorStore, VectorStoreError, get_vector_store
from app.db.session import tenant_session

log = logging.getLogger("dataviv.vault.semantic")

State = Literal["off", "ok", "unavailable"]
UNAVAILABLE = "Semantic search is unavailable right now, so these results are keyword matches only."


@dataclass
class Semantic:
    state: State
    hits: list[Hit] = field(default_factory=list)
    model: str | None = None

    @property
    def notice(self) -> str | None:
        return UNAVAILABLE if self.state == "unavailable" else None


def parts() -> tuple[Embedder, VectorStore] | None:
    """The embedder and its store, or None when semantic search is off or misconfigured."""
    try:
        emb = get_embedder()
    except EmbeddingError as exc:
        log.warning("semantic search off: %s", exc)
        return None
    if emb is None:
        return None
    store = get_vector_store(emb.name, emb.dim)
    return (emb, store) if store is not None else None


async def semantic_search(scope: Scope, query: str, limit: int) -> Semantic:
    p = parts()
    if p is None:
        return Semantic("off")
    emb, store = p
    try:
        [vector] = await emb.embed([query], "query")
        hits = await store.search(scope, vector, limit, get_settings().semantic_min_score)
    except (EmbeddingError, VectorStoreError) as exc:
        log.warning("semantic search unavailable: %s", exc)
        return Semantic("unavailable", model=emb.name)
    return Semantic("ok", hits, emb.name)


def fuse(rankings: Sequence[Sequence[uuid.UUID]], k: int) -> list[tuple[uuid.UUID, float]]:
    """Reciprocal rank fusion: score = sum of 1 / (k + rank) over the rankings an item appears in."""
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


# --------------------------------------------------------------------------- indexing


@dataclass(frozen=True)
class Passage:
    chunk_id: uuid.UUID
    matter_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    doc_type: str
    text: str


Progress = Callable[[int, int], Awaitable[Any] | Any]


async def index_document(
    firm_id: uuid.UUID, document_id: uuid.UUID, passages: list[Passage], on_progress: Progress | None = None
) -> str | None:
    """Replace a document's vectors with ``passages``. Returns the model name, or None if semantic is off.

    Raises EmbeddingError / VectorStoreError; the caller decides whether that is fatal.
    """
    p = parts()
    if p is None:
        return None
    emb, store = p
    await store.delete_documents(firm_id, [document_id])  # vectors of the version this one replaces
    if passages:
        await _embed_and_store(firm_id, emb, store, passages, on_progress)
    return emb.name


async def _embed_and_store(
    firm_id: uuid.UUID,
    emb: Embedder,
    store: VectorStore,
    passages: list[Passage],
    on_progress: Progress | None,
) -> None:
    vectors = await embed_batched(emb, [x.text for x in passages], "passage", on_progress)
    points = [
        Point(x.chunk_id, firm_id, x.matter_id, x.document_id, x.version_id, x.doc_type, v)
        for x, v in zip(passages, vectors, strict=True)
    ]
    for i in range(0, len(points), 128):
        await store.upsert(points[i : i + 128])


async def mark_embedded(firm_id: uuid.UUID, chunk_ids: list[uuid.UUID], model: str) -> None:
    from app.modules.vault.models import Chunk

    async with tenant_session(firm_id) as db:
        await db.execute(update(Chunk).where(Chunk.id.in_(chunk_ids)).values(embedding_model=model))


async def remove_documents(firm_id: uuid.UUID, document_ids: list[uuid.UUID]) -> None:
    """Best effort: a vector that outlives its passage is dropped at query time anyway."""
    p = parts()
    if p is None:
        return
    try:
        await p[1].delete_documents(firm_id, document_ids)
    except VectorStoreError as exc:
        log.warning("could not remove vectors of %s: %s", document_ids, exc)


_PENDING_SQL = text(
    """
    SELECT c.id, c.matter_id, c.document_id, c.version_id, d.doc_type, c.text
      FROM chunks c
      JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL AND d.current_version_id = c.version_id
     WHERE c.embedding_model IS DISTINCT FROM :model
     ORDER BY c.document_id, c.ordinal
    """
)


async def pending_count(db: AsyncSession, model: str) -> int:
    from app.modules.vault.models import Chunk, Document

    n = await db.scalar(
        select(func.count())
        .select_from(Chunk)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.deleted_at.is_(None), Chunk.embedding_model.is_distinct_from(model))
    )
    return int(n or 0)


async def backfill(firm_id: uuid.UUID, on_progress: Progress | None = None) -> int:
    """Embed every live passage of one firm not yet embedded with the current model. Returns how many.

    Runs as app_user under the firm's RLS context. Commits document by document, so it can be stopped
    and re-run. Raises if semantic search is off or the backends fail.
    """
    p = parts()
    if p is None:
        raise EmbeddingError(
            "Semantic search is off: set DATAVIV_EMBEDDINGS_BACKEND and DATAVIV_VECTOR_STORE"
        )
    emb, store = p
    async with tenant_session(firm_id) as db:
        rows = (await db.execute(_PENDING_SQL, {"model": emb.name})).all()
    by_doc: dict[uuid.UUID, list[Passage]] = {}
    for r in rows:
        by_doc.setdefault(r[2], []).append(Passage(r[0], r[1], r[2], r[3], r[4], r[5]))
    total, done = len(rows), 0
    for passages in by_doc.values():
        base = done

        async def step(n: int, _t: int, base: int = base) -> None:
            if on_progress is not None:
                r = on_progress(base + n, total)
                if r is not None and hasattr(r, "__await__"):
                    await r

        await _embed_and_store(firm_id, emb, store, passages, step)
        await mark_embedded(firm_id, [x.chunk_id for x in passages], emb.name)
        done += len(passages)
    return done


# --------------------------------------------------------------------------- loading hits from Postgres

_IDS = bindparam("ids", type_=ARRAY(PGUUID(as_uuid=True)))
_MATTERS = bindparam("matter_ids", type_=ARRAY(PGUUID(as_uuid=True)))
_DOC_IDS = bindparam("doc_ids", type_=ARRAY(PGUUID(as_uuid=True)))
_TYPES = bindparam("doc_types", type_=ARRAY(Text()))

# Search hits found by meaning. Same columns and snippet as the keyword query; the WHERE clause repeats
# the access scope, and RLS applies, so only passages the caller may see come back.
SEARCH_BY_IDS_SQL = text(
    """
    WITH q AS (
        SELECT websearch_to_tsquery('english', :query) || websearch_to_tsquery('simple', :query) AS tsq
    )
    SELECT c.id, c.document_id, d.title, c.matter_id, d.doc_type, c.section_path, c.page_from, c.page_to,
           ts_headline('english', c.text, q.tsq,
                       'MaxFragments=2, MaxWords=30, MinWords=12, FragmentDelimiter= … , StartSel=«, StopSel=»')
               AS snippet
      FROM chunks c
      JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL
      CROSS JOIN q
     WHERE c.id = ANY(:ids)
       AND c.matter_id = ANY(:matter_ids)
       AND (CAST(:doc_types AS text[]) IS NULL OR d.doc_type = ANY(:doc_types))
    """
).bindparams(_IDS, _MATTERS, _TYPES)

# Assistant / agent / workflow passages found by meaning (same columns as retrieval._RETRIEVE_SQL).
RETRIEVE_BY_IDS_SQL = text(
    """
    SELECT c.id, c.document_id, d.title, c.section_path, c.page_from, c.page_to, c.text
      FROM chunks c
      JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL
     WHERE c.id = ANY(:ids)
       AND c.matter_id = :matter_id
       AND (CAST(:doc_ids AS uuid[]) IS NULL OR c.document_id = ANY(:doc_ids))
    """
).bindparams(_IDS, _DOC_IDS)


async def current_firm(db: AsyncSession) -> uuid.UUID | None:
    """The firm whose RLS context this transaction runs in."""
    v = await db.scalar(text("SELECT current_setting('app.firm_id', true)"))
    return uuid.UUID(v) if v else None


# --------------------------------------------------------------------------- status


async def status(db: AsyncSession, firm_id: uuid.UUID) -> dict[str, Any]:
    s = get_settings()
    out: dict[str, Any] = {
        "enabled": False,
        "embeddings_backend": s.embeddings_backend,
        "vector_store": s.vector_store,
        "model": None,
        "collection": None,
        "reachable": None,
        "vectors": None,
        "pending_passages": None,
        "error": None,
    }
    try:
        emb = get_embedder()
    except EmbeddingError as exc:
        out["error"] = str(exc)
        return out
    if emb is None:
        return out
    store = get_vector_store(emb.name, emb.dim)
    if store is None:
        return out
    out.update(enabled=True, model=emb.name, collection=store.collection)
    out["pending_passages"] = await pending_count(db, emb.name)
    try:
        out["vectors"] = await store.count(firm_id)
        out["reachable"] = True
    except VectorStoreError as exc:
        out.update(reachable=False, error=str(exc))
    return out
