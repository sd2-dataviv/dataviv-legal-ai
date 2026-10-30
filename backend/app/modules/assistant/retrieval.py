"""Choose the passages an answer may use (AST-02). Permission checks happen before this is called.

Ask and Draft: keyword retrieval with the question's content words OR-ed together, so a natural
question ("What is the notice period for termination?") still matches clauses that use only some
of its words; ranked by PostgreSQL's cover density. When semantic search is on (Step 1c, ADR-019)
that ranking is fused with a meaning-based ranking from the vector store. Summarise: the chosen
documents' passages in reading order, round-robin across documents, until the context budget is used.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.vectors import Scope
from app.modules.assistant.verify import _STOP
from app.modules.vault import hybrid

_QUESTION_WORDS = {
    "please",
    "tell",
    "explain",
    "give",
    "list",
    "show",
    "find",
    "summarise",
    "summarize",
    "draft",
    "write",
    "prepare",
    "matter",
    "case",
    "brief",
    "detail",
    "details",
    "kindly",
}
# Hindi function words: the 'simple' dictionary keeps them, so they would match almost every passage
_HINDI_STOP = set(
    "का की के को में है हैं और से पर क्या यह वह था थी थे एक लिए तो भी ही कि जो इस उस कर किया गया".split()
)
_TERM = re.compile(r"\d+(?:\.\d+)+|[0-9A-Za-z\u0900-\u097f]+")  # keeps "12.1" whole


@dataclass
class Source:
    key: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section_path: str | None
    page_from: int
    page_to: int
    text: str

    def public(self) -> dict[str, object]:
        return {
            "key": self.key,
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "section_path": self.section_path,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "excerpt": " ".join(self.text.split())[:280],
        }


def query_terms(question: str, limit: int = 12) -> list[str]:
    terms: list[str] = []
    for t in _TERM.findall(question):
        low = t.lower()
        if len(low) < 2 or low in _STOP or low in _QUESTION_WORDS or low in _HINDI_STOP or low in terms:
            continue
        terms.append(low)
    return terms[:limit]


_DOC_IDS = bindparam("doc_ids", type_=ARRAY(PGUUID(as_uuid=True)))

_RETRIEVE_SQL = text(
    """
    WITH q AS (SELECT to_tsquery('english', :terms) || to_tsquery('simple', :terms) AS tsq)
    SELECT c.id, c.document_id, d.title, c.section_path, c.page_from, c.page_to, c.text
      FROM chunks c
      JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL
      CROSS JOIN q
     WHERE c.matter_id = :matter_id
       AND c.tsv @@ q.tsq
       AND (CAST(:doc_ids AS uuid[]) IS NULL OR c.document_id = ANY(:doc_ids))
     ORDER BY ts_rank_cd(c.tsv, q.tsq, 32) DESC, c.document_id, c.ordinal
     LIMIT :limit
    """
).bindparams(_DOC_IDS)

_IN_ORDER_SQL = text(
    """
    SELECT c.id, c.document_id, d.title, c.section_path, c.page_from, c.page_to, c.text,
           row_number() OVER (PARTITION BY c.document_id ORDER BY c.ordinal) AS rn, d.created_at
      FROM chunks c
      JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL
     WHERE c.matter_id = :matter_id
       AND (CAST(:doc_ids AS uuid[]) IS NULL OR c.document_id = ANY(:doc_ids))
     ORDER BY rn, d.created_at, c.document_id
     LIMIT 200
    """
).bindparams(_DOC_IDS)


def _sources(rows: list[tuple[object, ...]], budget: int, per_chunk: int, max_n: int) -> list[Source]:
    out: list[Source] = []
    used = 0
    for r in rows:
        body = str(r[6])
        if len(body) > per_chunk:
            body = body[:per_chunk].rsplit(" ", 1)[0] + " …"
        if out and used + len(body) > budget:
            break
        used += len(body)
        out.append(
            Source(
                key=f"S{len(out) + 1}",
                chunk_id=r[0],  # type: ignore[arg-type]
                document_id=r[1],  # type: ignore[arg-type]
                document_title=str(r[2]),
                section_path=r[3],  # type: ignore[arg-type]
                page_from=int(r[4]),  # type: ignore[call-overload]
                page_to=int(r[5]),  # type: ignore[call-overload]
                text=body,
            )
        )
        if len(out) >= max_n:
            break
    return out


async def retrieve(
    db: AsyncSession, matter_id: uuid.UUID, question: str, document_ids: list[uuid.UUID] | None, mode: str
) -> tuple[list[Source], list[str]]:
    """The passages an answer may use, best first, and the keyword terms searched for.

    Ask / Draft (and agent and workflow searches) fuse keyword ranking with meaning-based ranking when
    semantic search is on (ADR-019), so "can the buyer walk away?" finds the termination clause.
    Passages found only by meaning must clear DATAVIV_SEMANTIC_MIN_SCORE, so an unanswerable question
    still gets no passages. With semantic search off this is exactly the Step 2 keyword retrieval.
    """
    s = get_settings()
    doc_ids = document_ids or None
    if mode == "summarise":
        rows = (await db.execute(_IN_ORDER_SQL, {"matter_id": matter_id, "doc_ids": doc_ids})).all()
        return _sources([tuple(r) for r in rows], s.assistant_context_chars, s.assistant_chunk_chars, 12), []
    terms = query_terms(question)
    n = max(s.assistant_top_k * 3, 18)
    keyword_rows: list[tuple[object, ...]] = []
    if terms:
        keyword_rows = [
            tuple(r)
            for r in await db.execute(
                _RETRIEVE_SQL,
                {"terms": " | ".join(terms), "matter_id": matter_id, "doc_ids": doc_ids, "limit": n},
            )
        ]
    semantic = hybrid.Semantic("off")
    firm_id = await hybrid.current_firm(db) if hybrid.parts() is not None else None
    if firm_id is not None and question.strip():
        semantic = await hybrid.semantic_search(
            Scope(firm_id, frozenset({matter_id}), frozenset(doc_ids) if doc_ids else None), question, n
        )
    if semantic.state != "ok" or not semantic.hits:
        return _sources(
            keyword_rows, s.assistant_context_chars, s.assistant_chunk_chars, s.assistant_top_k
        ), terms
    by_id: dict[uuid.UUID, tuple[object, ...]] = {r[0]: r for r in keyword_rows}  # type: ignore[misc]
    missing = [h.chunk_id for h in semantic.hits if h.chunk_id not in by_id]
    if missing:
        found = await db.execute(
            hybrid.RETRIEVE_BY_IDS_SQL, {"ids": missing, "matter_id": matter_id, "doc_ids": doc_ids}
        )
        by_id.update({r[0]: tuple(r) for r in found})
    keyword_ids: list[uuid.UUID] = [r[0] for r in keyword_rows]  # type: ignore[misc]
    semantic_ids = [h.chunk_id for h in semantic.hits if h.chunk_id in by_id]
    ranked = hybrid.fuse([keyword_ids, semantic_ids], s.search_rrf_k)
    ordered = [by_id[cid] for cid, _ in ranked]
    return _sources(ordered, s.assistant_context_chars, s.assistant_chunk_chars, s.assistant_top_k), terms
