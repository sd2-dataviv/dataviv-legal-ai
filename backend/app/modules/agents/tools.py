"""Agent tools (AGT-03, AGT-04, AGT-05, AGT-06).

Every tool is a typed function: its arguments are a Pydantic model, checked before it runs. Tools act
as the person who started the run and only inside that run's matter (and, for a subagent, its one
document): no tool takes a matter id, and passages are addressed by the ids the run has already
seen (S1, S2…), never by raw database ids, so an agent cannot reach anything its user could not.

Read tools return an observation at once. Write tools return an `apply` function that the runtime
runs in the same transaction that marks the step done, after any approval the policy requires.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.security import Principal
from app.db.session import tenant_session
from app.modules.assistant.citations import parse_answer
from app.modules.assistant.retrieval import retrieve
from app.modules.assistant.verify import verify
from app.modules.drafts.models import Draft, DraftVersion
from app.modules.drafts.service import content_with_sources
from app.modules.vault.models import Chunk, Document

from .models import MemoryItem
from .spec import READ_TOOLS, TOOLS, WRITE_TOOLS

PASSAGE_CHARS = 1500  # stored per source, and the most an observation quotes from one passage
_KEY = re.compile(r"\[(S\d+)\]")

# --------------------------------------------------------------------------- arguments


class NoArgs(BaseModel):
    model_config = {"extra": "forbid"}


class ListDocuments(NoArgs):
    doc_type: str | None = Field(default=None, description="Only this type, e.g. agreement, notice, order")


class SearchDocuments(NoArgs):
    query: str = Field(min_length=2, max_length=300, description="Words to look for")
    document_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=20, description="Only these documents"
    )
    limit: int = Field(default=6, ge=1, le=8)


class ReadPassage(NoArgs):
    source: str = Field(pattern=r"^S\d+$", description="A passage id you have seen, e.g. S3")


class ReadDocument(NoArgs):
    document_id: uuid.UUID
    start: int = Field(default=0, ge=0, description="Passage number to start from (0 = the beginning)")
    max_passages: int = Field(default=4, ge=1, le=8)


class RecallMemory(NoArgs):
    query: str | None = Field(
        default=None, max_length=200, description="Words to look for; empty = everything"
    )
    kind: Literal["fact", "event", "party", "deadline", "note"] | None = None


class RememberFact(NoArgs):
    kind: Literal["fact", "event", "party", "deadline", "note"]
    subject: str = Field(min_length=2, max_length=200, description="Short label, e.g. 'Notice served'")
    content: str = Field(min_length=2, max_length=2000)
    event_date: date | None = Field(default=None, description="YYYY-MM-DD, for events and deadlines")
    source: str | None = Field(default=None, pattern=r"^S\d+$", description="The passage id it came from")


class CreateDraft(NoArgs):
    title: str = Field(min_length=3, max_length=200)
    kind: Literal["notice", "reply", "email", "memo", "other"] = "memo"
    content: str = Field(min_length=20, max_length=60_000, description="The text, citing passages as [S1]")


class Delegate(NoArgs):
    instruction: str = Field(min_length=10, max_length=3000, description="What each subagent should find")
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=8, description="One subagent per document")


class FinalAnswer(NoArgs):
    answer: str = Field(min_length=1, max_length=20_000, description="Your answer, citing passages as [S1]")


@dataclass(frozen=True)
class Tool:
    name: str
    args: type[BaseModel]
    description: str


REGISTRY: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "recall_memory",
            RecallMemory,
            "Facts, events, parties, deadlines and notes already saved for this matter.",
        ),
        Tool("list_documents", ListDocuments, "The documents you may use: id, title, type, pages, status."),
        Tool(
            "search_documents",
            SearchDocuments,
            "Search by words and meaning; returns passages with ids (S1, S2…) to cite.",
        ),
        Tool("read_passage", ReadPassage, "The full text of a passage you have already seen."),
        Tool("read_document", ReadDocument, "Read a document in order, a few passages at a time."),
        Tool(
            "remember_fact", RememberFact, "Save a fact or dated event to the matter memory, with its source."
        ),
        Tool(
            "create_draft", CreateDraft, "Save a document as a draft in the matter, citing passages as [S1]."
        ),
        Tool(
            "delegate_to_subagents",
            Delegate,
            "Give each document to its own subagent; you get their cited answers.",
        ),
        Tool("final_answer", FinalAnswer, "Finish: your answer for the lawyer, every fact cited."),
    )
}
assert set(REGISTRY) == TOOLS | {"final_answer"}, "spec.TOOLS and the registry must list the same tools"


def parameters(tool: Tool) -> str:
    """Compact parameter list for the prompt: name: type (required) — description."""
    schema = tool.args.model_json_schema()
    required = set(schema.get("required", []))
    parts = []
    for name, prop in schema.get("properties", {}).items():
        kind = prop.get("type") or "|".join(
            str(a.get("type") or a.get("enum") or "?")
            for a in prop.get("anyOf", [])
            if a.get("type") != "null"
        )
        if "enum" in prop:
            kind = "one of " + ", ".join(prop["enum"])
        desc = prop.get("description", "")
        parts.append(
            f"{name}: {kind}{'' if name not in required else ' (required)'}{' - ' + desc if desc else ''}"
        )
    return "; ".join(parts) or "none"


def check_arguments(tool: str, raw: dict[str, Any]) -> BaseModel:
    """Validate arguments; raises ValueError with a message the model can act on."""
    try:
        return REGISTRY[tool].args.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in err['loc']) or 'arguments'}: {err['msg']}" for err in e.errors()
        )
        raise ValueError(problems) from None


# --------------------------------------------------------------------------- context and results


@dataclass
class ToolContext:
    firm_id: uuid.UUID
    run_id: uuid.UUID
    run_name: str
    matter_id: uuid.UUID
    principal: Principal  # the person who started the run: the agent never has more access
    scope: list[uuid.UUID] | None  # a subagent's one document; None = the whole matter
    sources: list[dict[str, Any]]  # passages this run has seen; tools append to it
    spec: dict[str, Any]
    step_id: uuid.UUID
    remaining_cost: float = 0.0


Apply = Callable[[AsyncSession], Awaitable[dict[str, Any]]]


@dataclass
class ToolResult:
    observation: str
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "done"  # done | waiting (subagents) | failed
    apply: Apply | None = None


class ToolFailed(Exception):
    """The tool could not run; the message becomes the observation, and the agent can try again."""


def register(ctx: ToolContext, row: dict[str, Any]) -> str:
    """The run-wide id for a passage (the same passage always keeps its id)."""
    for s in ctx.sources:
        if s["chunk_id"] == row["chunk_id"]:
            return str(s["key"])
    key = f"S{len(ctx.sources) + 1}"
    ctx.sources.append({**row, "key": key, "text": str(row["text"])[:PASSAGE_CHARS]})
    return key


def _pages(a: int, b: int) -> str:
    return f"p. {a}" if a == b else f"pp. {a}-{b}"


def _quote(key: str, title: str, a: int, b: int, section: str | None, body: str, limit: int = 700) -> str:
    body = " ".join(body.split())
    if len(body) > limit:
        body = body[:limit].rsplit(" ", 1)[0] + " …"
    where = f"{_pages(a, b)}{', ' + section if section else ''}"
    return f'[{key}] {title}, {where}: "{body}"'


def _in_scope(ctx: ToolContext, ids: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    if ctx.scope is None:
        return ids
    if ids is None:
        return list(ctx.scope)
    outside = [i for i in ids if i not in ctx.scope]
    if outside:
        raise ToolFailed("You may only use the document you were given")
    return ids


async def _documents(db: AsyncSession, ctx: ToolContext, ids: list[uuid.UUID]) -> dict[uuid.UUID, Document]:
    rows = (
        await db.scalars(
            select(Document).where(
                Document.id.in_(ids), Document.matter_id == ctx.matter_id, Document.deleted_at.is_(None)
            )
        )
    ).all()
    found = {d.id: d for d in rows}
    if len(found) != len(set(ids)):
        raise ToolFailed("One or more of those documents are not in this matter (call list_documents)")
    return found


# --------------------------------------------------------------------------- read tools


async def recall_memory(ctx: ToolContext, a: RecallMemory) -> ToolResult:
    async with tenant_session(ctx.firm_id) as db:
        stmt = (
            select(MemoryItem, Document.title)
            .outerjoin(Document, Document.id == MemoryItem.document_id)
            .where(MemoryItem.matter_id == ctx.matter_id, MemoryItem.deleted_at.is_(None))
        )
        if a.kind:
            stmt = stmt.where(MemoryItem.kind == a.kind)
        if a.query:
            stmt = stmt.where(
                or_(
                    text("matter_memory.tsv @@ plainto_tsquery('simple', :q)").bindparams(q=a.query),
                    MemoryItem.subject.ilike(f"%{a.query}%"),
                    MemoryItem.content.ilike(f"%{a.query}%"),
                )
            )
        rows = (
            await db.execute(
                stmt.order_by(MemoryItem.event_date.nulls_last(), MemoryItem.created_at).limit(40)
            )
        ).all()
    if not rows:
        return ToolResult(
            "The matter memory has nothing" + (" matching that." if a.query or a.kind else " yet.")
        )
    lines = []
    for i, (m, title) in enumerate(rows, start=1):
        when = f" ({m.event_date:%d %B %Y})" if m.event_date else ""
        src = (
            f" [from {title}, {_pages(m.page_from, m.page_to or m.page_from)}]"
            if title and m.page_from
            else ""
        )
        lines.append(f"[M{i}] {m.kind} - {m.subject}{when}: {m.content}{src}")
    note = "Memory items are not passages: to cite one, find its passage with search_documents."
    return ToolResult("\n".join(lines) + "\n" + note, {"items": len(rows)})


async def list_documents(ctx: ToolContext, a: ListDocuments) -> ToolResult:
    async with tenant_session(ctx.firm_id) as db:
        stmt = select(Document).where(Document.matter_id == ctx.matter_id, Document.deleted_at.is_(None))
        if ctx.scope is not None:
            stmt = stmt.where(Document.id.in_(ctx.scope))
        if a.doc_type:
            stmt = stmt.where(Document.doc_type == a.doc_type)
        docs = (await db.scalars(stmt.order_by(Document.created_at).limit(200))).all()
    if not docs:
        return ToolResult(
            "There are no documents" + (f" of type {a.doc_type}." if a.doc_type else " in this matter.")
        )
    lines = [
        f"- {d.id} | {d.title} | {d.doc_type} | {d.page_count or '?'} pages | {d.status}"
        + (" (some pages wait for OCR)" if d.ocr_pending_pages else "")
        for d in docs
    ]
    return ToolResult("id | title | type | pages | status\n" + "\n".join(lines), {"documents": len(docs)})


async def search_documents(ctx: ToolContext, a: SearchDocuments) -> ToolResult:
    ids = _in_scope(ctx, a.document_ids)
    async with tenant_session(ctx.firm_id) as db:
        if a.document_ids:
            await _documents(db, ctx, a.document_ids)
        found, terms = await retrieve(db, ctx.matter_id, a.query, ids, "ask")
    found = found[: a.limit]
    if not found:
        return ToolResult(
            f"No passages match {a.query!r}"
            + (f" (searched for: {', '.join(terms)})" if terms else "")
            + ". Try other words.",
            {"found": 0},
        )
    lines = []
    for s in found:
        key = register(
            ctx,
            {
                "chunk_id": str(s.chunk_id),
                "document_id": str(s.document_id),
                "document_title": s.document_title,
                "section_path": s.section_path,
                "page_from": s.page_from,
                "page_to": s.page_to,
                "text": s.text,
            },
        )
        lines.append(_quote(key, s.document_title, s.page_from, s.page_to, s.section_path, s.text))
    return ToolResult("\n".join(lines), {"found": len(found), "keys": [ln.split("]")[0][1:] for ln in lines]})


async def read_passage(ctx: ToolContext, a: ReadPassage) -> ToolResult:
    src = next((s for s in ctx.sources if s["key"] == a.source), None)
    if src is None:
        raise ToolFailed(f"{a.source} is not a passage you have seen; search first")
    async with tenant_session(ctx.firm_id) as db:
        chunk = await db.get(Chunk, uuid.UUID(src["chunk_id"]))
    if chunk is None or chunk.matter_id != ctx.matter_id:
        raise ToolFailed("That passage is no longer available")
    body = chunk.text if len(chunk.text) <= 4000 else chunk.text[:4000] + " …"
    return ToolResult(
        _quote(
            a.source, src["document_title"], chunk.page_from, chunk.page_to, chunk.section_path, body, 4000
        ),
        {"source": a.source},
    )


async def read_document(ctx: ToolContext, a: ReadDocument) -> ToolResult:
    _in_scope(ctx, [a.document_id])
    async with tenant_session(ctx.firm_id) as db:
        doc = (await _documents(db, ctx, [a.document_id]))[a.document_id]
        base = select(Chunk).where(Chunk.document_id == doc.id)
        if doc.current_version_id:
            base = base.where(Chunk.version_id == doc.current_version_id)
        total = await db.scalar(select(func.count()).select_from(base.subquery()))
        chunks = (await db.scalars(base.order_by(Chunk.ordinal).offset(a.start).limit(a.max_passages))).all()
    if not chunks:
        if not total:
            return ToolResult(f"“{doc.title}” has no readable text yet (it may be waiting for OCR).")
        return ToolResult(f"“{doc.title}” has only {total} passages; there is nothing from {a.start}.")
    lines = []
    for c in chunks:
        key = register(
            ctx,
            {
                "chunk_id": str(c.id),
                "document_id": str(doc.id),
                "document_title": doc.title,
                "section_path": c.section_path,
                "page_from": c.page_from,
                "page_to": c.page_to,
                "text": c.text,
            },
        )
        lines.append(_quote(key, doc.title, c.page_from, c.page_to, c.section_path, c.text, 1200))
    end = a.start + len(chunks)
    more = (
        f"To read on, call read_document with start={end}."
        if end < (total or 0)
        else "That is the end of the document."
    )
    return ToolResult(
        f"“{doc.title}”, passages {a.start + 1}-{end} of {total}:\n" + "\n".join(lines) + "\n" + more,
        {"document_id": str(doc.id), "start": a.start, "end": end, "total": total},
    )


# --------------------------------------------------------------------------- write tools


def _source(ctx: ToolContext, key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    src = next((s for s in ctx.sources if s["key"] == key), None)
    if src is None:
        raise ToolFailed(f"{key} is not a passage you have seen")
    return src


async def remember_fact(ctx: ToolContext, a: RememberFact) -> ToolResult:
    src = _source(ctx, a.source)

    async def apply(db: AsyncSession) -> dict[str, Any]:
        item = MemoryItem(
            firm_id=ctx.firm_id,
            matter_id=ctx.matter_id,
            kind=a.kind,
            subject=a.subject.strip(),
            content=a.content.strip(),
            event_date=a.event_date,
            document_id=uuid.UUID(src["document_id"]) if src else None,
            chunk_id=uuid.UUID(src["chunk_id"]) if src else None,
            page_from=src["page_from"] if src else None,
            page_to=src["page_to"] if src else None,
            source="agent",
            agent_run_id=ctx.run_id,
            created_by=ctx.principal.user_id,
        )
        db.add(item)
        await db.flush()
        await audit.record(
            db,
            ctx.principal,
            "memory.add",
            "matter_memory",
            item.id,
            ctx.matter_id,
            {"kind": a.kind, "subject": a.subject, "agent_run_id": str(ctx.run_id)},
        )
        return {"memory_id": str(item.id)}

    return ToolResult(
        f"Saved to the matter memory: {a.kind} - {a.subject}" + (f" (source {a.source})" if a.source else ""),
        {"kind": a.kind, "subject": a.subject},
        apply=apply,
    )


async def create_draft(ctx: ToolContext, a: CreateDraft) -> ToolResult:
    sentences = parse_answer(a.content)
    known = {s["key"]: s for s in ctx.sources}
    cited = [k for k in dict.fromkeys(k for s in sentences for k in s.cites) if k in known]
    unknown = sorted({k for s in sentences for k in s.cites if k not in known})
    check = verify(sentences, {k: known[k]["text"] for k in cited}, "draft")
    public = [{**known[k], "key": k} for k in cited]
    content, numbered = content_with_sources([s.as_dict() for s in sentences], public, a.content)

    async def apply(db: AsyncSession) -> dict[str, Any]:
        p = ctx.principal
        draft = Draft(
            firm_id=p.firm_id,
            matter_id=ctx.matter_id,
            title=a.title.strip()[:200],
            kind=a.kind,
            status="draft",
            current_version_no=1,
            sources=numbered,
            created_by=p.user_id,
            updated_by=p.user_id,
        )
        db.add(draft)
        await db.flush()
        db.add(
            DraftVersion(
                firm_id=p.firm_id,
                matter_id=ctx.matter_id,
                draft_id=draft.id,
                version_no=1,
                content=content,
                note=f"Created by the agent “{ctx.run_name}”"[:500],
                created_by=p.user_id,
            )
        )
        await db.flush()
        await audit.record(
            db,
            p,
            "draft.create",
            "draft",
            draft.id,
            ctx.matter_id,
            {"title": draft.title, "kind": a.kind, "from_agent_run": str(ctx.run_id)},
        )
        return {"draft_id": str(draft.id)}

    note = f" Unknown passage ids were dropped: {', '.join(unknown)}." if unknown else ""
    return ToolResult(
        f"Draft “{a.title}” saved in the matter with {len(numbered)} cited sources "
        f"({check['supported']} sentences supported, {check['partial']} partly, {check['unsupported']} not).{note}",
        {"title": a.title, "kind": a.kind, "sources": len(numbered), "verification": check},
        apply=apply,
    )


async def delegate_to_subagents(ctx: ToolContext, a: Delegate) -> ToolResult:
    """Creates one subagent run per document; the runtime waits for all of them (see runtime.py)."""
    from .runtime import spawn_children  # the runtime owns run creation

    ids = list(dict.fromkeys(a.document_ids))
    async with tenant_session(ctx.firm_id) as db:
        docs = await _documents(db, ctx, ids)
    titles = {str(i): docs[i].title for i in ids}

    async def apply(db: AsyncSession) -> dict[str, Any]:
        children = await spawn_children(db, ctx, a.instruction, [(i, docs[i].title) for i in ids])
        return {"children": [str(c) for c in children]}

    return ToolResult(
        f"Started {len(ids)} subagents, one per document: " + "; ".join(titles.values()),
        {"documents": titles, "instruction": a.instruction},
        status="waiting",
        apply=apply,
    )


Runner = Callable[[ToolContext, Any], Awaitable[ToolResult]]
RUNNERS: dict[str, Runner] = {
    "recall_memory": recall_memory,
    "list_documents": list_documents,
    "search_documents": search_documents,
    "read_passage": read_passage,
    "read_document": read_document,
    "remember_fact": remember_fact,
    "create_draft": create_draft,
    "delegate_to_subagents": delegate_to_subagents,
}
assert set(RUNNERS) == READ_TOOLS | WRITE_TOOLS


def rekey(answer: str, mapping: dict[str, str]) -> str:
    """Rewrite [S3] citations with another run's ids (a subagent's answer, seen by its parent)."""
    return _KEY.sub(lambda m: f"[{mapping.get(m.group(1), m.group(1))}]", answer)
