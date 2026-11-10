"""Drafting studio service: generate and revise general documents (streamed), firm-wide history,
manual edits, soft delete and Word export. Every model call is audited like the Assistant's ("ai.call")."""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound, Unavailable, Unprocessable
from app.core.llm import Done, LLMError, Message, ModelNotAllowed, Notice, TextDelta, gateway_for
from app.core.models import Firm, User
from app.core.security import Principal
from app.db.session import tenant_session
from app.modules.studio import prompts
from app.modules.studio.docx import build_studio_docx
from app.modules.studio.models import StudioDocument
from app.modules.studio.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentUpdate,
    GenerateRequest,
    Person,
    ReviseRequest,
    RevisionOut,
)
from app.modules.studio.templates import BY_KEY, TEMPLATES, Template

log = logging.getLogger("dataviv.studio")


class StudioSettings(BaseSettings):
    """DATAVIV_STUDIO_* in backend/.env. Kept here so the add-on needs no change to core settings."""

    model_config = SettingsConfigDict(
        env_prefix="DATAVIV_STUDIO_", env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore"
    )
    # A full contract runs to 3,000-5,000 tokens; reasoning models also spend some of this on thinking.
    max_output_tokens: int = 6000


settings = StudioSettings()
LOOP_WARNING = (
    "Stopped early: the model started repeating itself. The repeated part was removed; check the end of "
    "the document, and give more facts or instructions if it is too thin."
)
EDITORS = ("partner", "admin")  # besides the author: who may edit or delete a firm document


def templates() -> list[dict[str, Any]]:
    return [t.as_dict() for t in TEMPLATES]


def _staff(p: Principal) -> None:
    if p.role == "client":
        raise Forbidden("Client users cannot use the drafting studio")


def _may_change(p: Principal, doc: StudioDocument) -> None:
    if doc.created_by != p.user_id and p.role not in EDITORS:
        raise Forbidden("Only the author, a partner or an admin can change this document")


def _template(key: str) -> Template:
    return BY_KEY.get(key) or BY_KEY["custom"]


async def _doc(db: AsyncSession, p: Principal, doc_id: uuid.UUID) -> StudioDocument:
    _staff(p)
    doc = await db.get(StudioDocument, doc_id)
    if doc is None or doc.deleted_at is not None:
        raise NotFound("Document not found")
    return doc


async def _people(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, Person]:
    if not ids:
        return {}
    rows = (await db.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {i: Person(id=i, name=n) for i, n in rows}


def _out(doc: StudioDocument, people: dict[uuid.UUID, Person], versions: int = 1) -> DocumentOut:
    t = _template(doc.template)
    return DocumentOut(
        id=doc.id,
        family_id=doc.family_id,
        parent_id=doc.parent_id,
        template=doc.template,
        template_name=t.name,
        category=t.category,
        title=doc.title,
        summary=prompts.describe(doc.inputs, t),
        status=doc.status,
        error=doc.error,
        model=doc.model,
        created_by=people.get(doc.created_by) or Person(id=doc.created_by, name="Unknown"),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        versions=versions,
    )


# --------------------------------------------------------------------------- history


async def list_documents(
    db: AsyncSession, p: Principal, q: str | None, template: str | None, mine: bool, limit: int
) -> list[DocumentOut]:
    """The firm's documents, newest first, one row per document (its latest version)."""
    _staff(p)
    family = func.coalesce(StudioDocument.root_id, StudioDocument.id)
    live = StudioDocument.deleted_at.is_(None)
    ranked = select(
        StudioDocument.id.label("id"),
        family.label("family"),
        func.row_number().over(partition_by=family, order_by=StudioDocument.created_at.desc()).label("rn"),
        func.count().over(partition_by=family).label("n"),
    ).where(live)
    if template:
        ranked = ranked.where(StudioDocument.template == template)
    if mine:
        ranked = ranked.where(StudioDocument.created_by == p.user_id)
    sub = ranked.subquery()
    stmt = (
        select(StudioDocument, sub.c.n)
        .join(sub, sub.c.id == StudioDocument.id)
        .where(sub.c.rn == 1)
        .order_by(StudioDocument.updated_at.desc())
        .limit(limit)
    )
    if q and q.strip():
        words = text(
            "tsv @@ (websearch_to_tsquery('english', :q) || websearch_to_tsquery('simple', :q))"
        ).bindparams(q=q.strip())
        hits = (
            await db.scalars(
                select(family)
                .where(live, or_(StudioDocument.title.ilike(f"%{q.strip()}%"), words))
                .limit(1000)
            )
        ).all()
        stmt = stmt.where(sub.c.family.in_(set(hits) or {uuid.UUID(int=0)}))
    rows = (await db.execute(stmt)).all()
    people = await _people(db, {d.created_by for d, _ in rows})
    return [_out(d, people, int(n)) for d, n in rows]


async def detail(db: AsyncSession, p: Principal, doc_id: uuid.UUID) -> DocumentDetail:
    doc = await _doc(db, p, doc_id)
    family = (
        await db.scalars(
            select(StudioDocument)
            .where(
                or_(StudioDocument.id == doc.family_id, StudioDocument.root_id == doc.family_id),
                StudioDocument.deleted_at.is_(None),
            )
            .order_by(StudioDocument.created_at)
        )
    ).all()
    people = await _people(db, {d.created_by for d in family} | {doc.created_by})
    base = _out(doc, people, len(family))
    return DocumentDetail(
        **base.model_dump(),
        inputs={k: str(v) for k, v in doc.inputs.items()},
        instructions=doc.instructions,
        content=doc.content,
        input_tokens=doc.input_tokens,
        output_tokens=doc.output_tokens,
        latency_ms=doc.latency_ms,
        cost_usd=doc.cost_usd,
        revisions=[
            RevisionOut(
                id=d.id,
                parent_id=d.parent_id,
                instructions=d.instructions,
                status=d.status,
                created_by=people.get(d.created_by) or Person(id=d.created_by, name="Unknown"),
                created_at=d.created_at,
            )
            for d in family
        ],
    )


async def update_document(
    db: AsyncSession, p: Principal, doc_id: uuid.UUID, body: DocumentUpdate
) -> DocumentDetail:
    doc = await _doc(db, p, doc_id)
    _may_change(p, doc)
    if doc.status == "streaming":
        raise Conflict("The document is still being written")
    changed: dict[str, Any] = {}
    if body.title is not None and body.title.strip() != doc.title:
        doc.title = changed["title"] = body.title.strip()
    if body.content is not None and body.content != doc.content:
        doc.content = body.content
        changed["content_chars"] = len(body.content)
        if doc.status == "failed" and body.content.strip():
            doc.status, doc.error = "complete", None  # the lawyer finished it by hand
    if changed:
        doc.updated_at = func.clock_timestamp()
        await audit.record(db, p, "studio.update", "studio_document", doc.id, None, changed)
        await db.flush()
        await db.refresh(doc)
    return await detail(db, p, doc.id)


async def delete_document(db: AsyncSession, p: Principal, doc_id: uuid.UUID) -> None:
    """Soft-deletes the document with all its versions."""
    doc = await _doc(db, p, doc_id)
    _may_change(p, doc)
    fam = doc.family_id
    result = await db.execute(
        update(StudioDocument)
        .where(
            or_(StudioDocument.id == fam, StudioDocument.root_id == fam), StudioDocument.deleted_at.is_(None)
        )
        .values(deleted_at=func.now())
    )
    await audit.record(
        db,
        p,
        "studio.delete",
        "studio_document",
        fam,
        None,
        {"versions": result.rowcount},  # type: ignore[attr-defined]
    )


# --------------------------------------------------------------------------- generate / revise


@dataclass
class Prepared:
    principal: Principal
    doc_id: uuid.UUID
    family_id: uuid.UUID
    parent_id: uuid.UUID | None
    template: str
    title: str
    model: str
    policy: dict[str, Any]
    messages: list[Message]


async def _model(db: AsyncSession, p: Principal, requested: str | None) -> tuple[dict[str, Any], str]:
    firm = await db.get(Firm, p.firm_id)
    policy = firm.model_policy if firm else {}
    try:
        return policy, gateway_for(policy).policy.resolve("draft", requested)
    except ModelNotAllowed as e:
        raise Unprocessable(str(e)) from e
    except LLMError as e:
        raise Unavailable(str(e)) from e


async def prepare_generate(db: AsyncSession, p: Principal, body: GenerateRequest) -> Prepared:
    _staff(p)
    t = BY_KEY.get(body.template)
    if t is None:
        raise Unprocessable(f"Unknown template {body.template!r}. Choose one from GET /v1/studio/templates")
    keys = {f.key for f in t.fields}
    unknown = sorted(set(body.inputs) - keys)
    if unknown:
        raise Unprocessable(f"Unknown field(s) for {t.name}: {', '.join(unknown)}")
    inputs = {k: v.strip() for k, v in body.inputs.items() if v.strip()}
    missing = [f.label for f in t.fields if f.required and f.key not in inputs]
    if missing and not body.instructions.strip():
        raise Unprocessable(f"Fill in {', '.join(missing)}, or describe the document in the instructions")
    policy, model = await _model(db, p, body.model)
    title = (body.title or "").strip()
    if not title:
        lead = prompts.describe(inputs, t)
        title = f"{t.name}: {lead}" if lead else t.name
    doc = StudioDocument(
        firm_id=p.firm_id,
        template=t.key,
        title=title[:300],
        inputs=inputs,
        instructions=body.instructions.strip(),
        status="streaming",
        model=model,
        created_by=p.user_id,
    )
    db.add(doc)
    await db.flush()
    await audit.record(
        db,
        p,
        "studio.generate",
        "studio_document",
        doc.id,
        None,
        {"template": t.key, "fields": sorted(inputs), "instruction_chars": len(doc.instructions)},
    )
    return Prepared(
        p, doc.id, doc.id, None, t.key, doc.title, model, policy,
        prompts.build_messages(t, inputs, doc.instructions),
    )  # fmt: skip


async def prepare_revise(db: AsyncSession, p: Principal, doc_id: uuid.UUID, body: ReviseRequest) -> Prepared:
    parent = await _doc(db, p, doc_id)
    if parent.status != "complete" or not parent.content.strip():
        raise Conflict("Only a finished document can be revised")
    t = _template(parent.template)
    root = parent if parent.root_id is None else await db.get(StudioDocument, parent.root_id)
    policy, model = await _model(db, p, body.model)
    inputs = {k: str(v) for k, v in parent.inputs.items()}
    doc = StudioDocument(
        firm_id=p.firm_id,
        root_id=parent.family_id,
        parent_id=parent.id,
        template=parent.template,
        title=parent.title,
        inputs=inputs,
        instructions=body.instructions.strip(),
        status="streaming",
        model=model,
        created_by=p.user_id,
    )
    db.add(doc)
    await db.flush()
    await audit.record(
        db,
        p,
        "studio.revise",
        "studio_document",
        doc.id,
        None,
        {"parent_id": str(parent.id), "instruction_chars": len(doc.instructions)},
    )
    messages = prompts.revise_messages(
        t, inputs, root.instructions if root else "", parent.content, body.instructions
    )
    return Prepared(
        p, doc.id, parent.family_id, parent.id, parent.template, doc.title, model, policy, messages
    )


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def _prompt_hash(messages: list[Message]) -> str:
    h = hashlib.sha256()
    for m in messages:
        h.update(m.role.encode() + b"\x00" + m.content.encode() + b"\x00")
    return h.hexdigest()


async def _finish(
    prep: Prepared, values: dict[str, Any], audit_detail: dict[str, Any] | None
) -> DocumentDetail:
    async with tenant_session(prep.principal.firm_id) as db:
        doc = await db.get(StudioDocument, prep.doc_id)
        assert doc is not None
        for k, v in values.items():
            setattr(doc, k, v)
        doc.updated_at = func.clock_timestamp()
        if audit_detail is not None:
            await audit.record(db, prep.principal, "ai.call", "studio_document", doc.id, None, audit_detail)
        await db.flush()
        await db.refresh(doc)
        return await detail(db, prep.principal, doc.id)


async def run(prep: Prepared) -> AsyncIterator[str]:
    """SSE stream: meta → (notice | delta)* → done, or error. Always leaves the row final."""
    finished = False
    yield sse(
        "meta",
        {
            "document_id": prep.doc_id,
            "family_id": prep.family_id,
            "parent_id": prep.parent_id,
            "template": prep.template,
            "title": prep.title,
            "model": prep.model,
        },
    )
    try:
        buf: list[str] = []
        done: Done | None = None
        guard = prompts.LoopGuard()
        items = gateway_for(prep.policy).stream(
            "draft", prep.messages, model=prep.model, max_tokens=settings.max_output_tokens
        )
        try:
            async for item in items:
                if isinstance(item, TextDelta):
                    buf.append(item.text)
                    yield sse("delta", {"text": item.text})
                    if guard.feed(item.text):  # stop the model instead of paying for the loop
                        yield sse("notice", {"text": LOOP_WARNING})
                        break
                elif isinstance(item, Notice):
                    yield sse("notice", {"text": item.text})
                elif isinstance(item, Done):
                    done = item
        except LLMError as e:
            out = await _finish(
                prep,
                {"content": prompts.clean("".join(buf)), "status": "failed", "error": str(e)},
                {"task": "draft", "model": prep.model, "error": str(e), "status": e.status},
            )
            finished = True
            yield sse(
                "error",
                {
                    "detail": str(e),
                    "status": e.status,
                    "retry_after": e.retry_after,
                    "document": out.model_dump(mode="json"),
                },
            )
            return
        finally:
            close = getattr(items, "aclose", None)  # an async generator: closing it ends the model request
            if close is not None:
                await close()

        content = prompts.clean("".join(buf))
        usage = done.usage if done else None
        warning = None  # stored in `error` on a complete document: shown, but the text is usable
        if guard.looping:
            content, warning = prompts.trim_loop(content, guard.looping), LOOP_WARNING
        elif usage and usage.output_tokens >= settings.max_output_tokens:
            warning = (
                f"The model reached the length limit ({settings.max_output_tokens:,} tokens), so the end may "
                "be missing. Ask for the rest with a revision, or raise DATAVIV_STUDIO_MAX_OUTPUT_TOKENS."
            )
        values: dict[str, Any] = {
            "content": content,
            "status": "complete" if content else "failed",
            "error": warning if content else "The model returned no text. Try again, or shorten the request.",
            "model": done.model if done else prep.model,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "latency_ms": done.latency_ms if done else None,
            "cost_usd": round(done.cost_usd, 6) if done else None,
        }
        out = await _finish(
            prep,
            values,
            {
                "task": "draft",
                "feature": "studio",
                "template": prep.template,
                "model": values["model"],
                "prompt_sha256": _prompt_hash(prep.messages),
                "input_tokens": values["input_tokens"],
                "output_tokens": values["output_tokens"],
                "latency_ms": values["latency_ms"],
                "cost_usd": values["cost_usd"],
            },
        )
        finished = True
        yield sse("done", out.model_dump(mode="json"))
    finally:
        if not finished:  # client went away or something unexpected: never leave a "streaming" row
            try:
                await asyncio.shield(
                    _finish(prep, {"status": "failed", "error": "Writing was interrupted."}, None)
                )
            except Exception:  # best effort while unwinding: never mask the original problem
                log.exception("Could not mark studio document %s as interrupted", prep.doc_id)


# --------------------------------------------------------------------------- export


async def export_docx(db: AsyncSession, p: Principal, doc_id: uuid.UUID) -> tuple[bytes, str]:
    doc = await _doc(db, p, doc_id)
    if doc.status == "streaming" or not doc.content.strip():
        raise Conflict("The document has no finished text to export yet")
    body, notes = prompts.split_notes(doc.content)
    footer = (
        f"Prepared with Dataviv AI OS{f' ({doc.model})' if doc.model else ''} on {doc.updated_at:%d %b %Y}. "
        "A draft for review by an advocate: check every fact, placeholder and statutory reference before use."
    )
    data = build_studio_docx(body, notes, footer)
    await audit.record(db, p, "studio.export", "studio_document", doc.id, None, {"format": "docx"})
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in doc.title)[:60].strip() or "document"
    return data, f"{safe}.docx"
