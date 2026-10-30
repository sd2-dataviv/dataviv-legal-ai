"""Assistant business logic (AST-01..04, 06, 08, 09, 10; FND-05, FND-06). No HTTP here.

An answer is produced in two phases so permission errors surface as ordinary HTTP errors before
any streaming starts:

1. `prepare_ask` (one transaction): check access, validate chosen documents and model, retrieve
   sources, store the question and an empty "streaming" answer, audit the question.
2. `run_ask` (async generator, its own short transactions): stream the model's answer as SSE
   events, parse citations, verify every sentence, store the result and audit the model call.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unavailable, Unprocessable
from app.core.llm import Done, LLMError, Message, ModelNotAllowed, Notice, TaskClass, TextDelta, gateway_for
from app.core.models import Firm
from app.core.permissions import require_matter_access
from app.core.security import Principal
from app.db.session import tenant_session
from app.modules.assistant.citations import Sentence, parse_answer, strip_markers
from app.modules.assistant.docx import build_docx
from app.modules.assistant.models import Thread, ThreadMessage
from app.modules.assistant.prompts import NO_SOURCES, VERIFY_SCHEMA, build_messages, verifier_messages
from app.modules.assistant.retrieval import Source, retrieve
from app.modules.assistant.schemas import AskRequest, MessageOut, ModelPolicyIn, ThreadDetail, ThreadOut
from app.modules.assistant.verify import merge_model_labels, verify
from app.modules.vault.models import Document

log = logging.getLogger("dataviv.assistant")
NEW_THREAD = "New thread"
TASK_FOR_MODE: dict[str, TaskClass] = {"ask": "qa", "summarise": "qa", "draft": "draft"}


# --------------------------------------------------------------------------- threads


async def _thread(db: AsyncSession, p: Principal, thread_id: uuid.UUID) -> Thread:
    thread = await db.get(Thread, thread_id)
    if thread is None:
        raise NotFound("Thread not found")
    try:
        await require_matter_access(db, p, thread.matter_id)
    except NotFound:
        raise NotFound("Thread not found") from None  # same answer as a thread that does not exist
    return thread


def _thread_out(t: Thread, count: int) -> ThreadOut:
    return ThreadOut(
        id=t.id,
        matter_id=t.matter_id,
        title=t.title,
        created_by=t.created_by,
        created_at=t.created_at,
        updated_at=t.updated_at,
        message_count=count,
    )


async def create_thread(db: AsyncSession, p: Principal, matter_id: uuid.UUID, title: str | None) -> ThreadOut:
    await require_matter_access(db, p, matter_id)
    thread = Thread(
        firm_id=p.firm_id,
        matter_id=matter_id,
        title=(title or "").strip() or NEW_THREAD,
        created_by=p.user_id,
    )
    db.add(thread)
    await db.flush()
    await db.refresh(thread)
    await audit.record(
        db, p, "assistant.thread_create", "thread", thread.id, matter_id, {"title": thread.title}
    )
    return _thread_out(thread, 0)


async def list_threads(
    db: AsyncSession, p: Principal, matter_id: uuid.UUID, q: str | None
) -> list[ThreadOut]:
    """Threads of a matter, newest first; `q` searches titles and every message (AST-10)."""
    await require_matter_access(db, p, matter_id)
    counts = (
        select(ThreadMessage.thread_id, func.count().label("n")).group_by(ThreadMessage.thread_id).subquery()
    )
    stmt = (
        select(Thread, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.thread_id == Thread.id)
        .where(Thread.matter_id == matter_id)
        .order_by(Thread.updated_at.desc())
        .limit(200)
    )
    if q and q.strip():
        matches = text(
            "thread_messages.tsv @@ (websearch_to_tsquery('english', :q) || websearch_to_tsquery('simple', :q))"
        ).bindparams(q=q.strip())
        hit = select(ThreadMessage.id).where(ThreadMessage.thread_id == Thread.id, matches)
        stmt = stmt.where(or_(Thread.title.ilike(f"%{q.strip()}%"), hit.exists()))
    return [_thread_out(t, n) for t, n in (await db.execute(stmt)).all()]


def _message_out(m: ThreadMessage) -> MessageOut:
    out = MessageOut.model_validate(m)
    out.cost_usd = float(m.cost_usd) if m.cost_usd is not None else None
    return out


async def thread_detail(db: AsyncSession, p: Principal, thread_id: uuid.UUID) -> ThreadDetail:
    thread = await _thread(db, p, thread_id)
    msgs = (
        await db.scalars(
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
            .order_by(ThreadMessage.created_at)
        )
    ).all()
    return ThreadDetail(
        **_thread_out(thread, len(msgs)).model_dump(), messages=[_message_out(m) for m in msgs]
    )


# --------------------------------------------------------------------------- models and policy


def _gateway(policy: dict[str, Any]) -> Any:
    try:
        return gateway_for(policy)
    except LLMError as e:
        raise Unavailable(str(e)) from e


async def models(db: AsyncSession, p: Principal) -> dict[str, Any]:
    firm = await db.get(Firm, p.firm_id)
    gw = _gateway(firm.model_policy if firm else {})
    return {"backend": gw.backend.name, "catalogue": gw.backend.models(), **gw.policy.as_dict()}


async def set_model_policy(db: AsyncSession, p: Principal, body: ModelPolicyIn) -> dict[str, Any]:
    if not p.is_admin:
        raise Forbidden("Only firm admins can change the model policy")
    firm = await db.get(Firm, p.firm_id)
    if firm is None:  # pragma: no cover - the principal's firm always exists
        raise NotFound("Firm not found")
    catalogue = _gateway({}).backend.models()
    unknown = [m for m in [*body.allowed, *body.routes.values()] if m not in catalogue]
    if unknown:
        raise Unprocessable(
            f"Unknown model(s): {', '.join(sorted(set(unknown)))}. Available: {', '.join(catalogue)}"
        )
    if any(m not in body.allowed for m in body.routes.values()):
        raise Unprocessable("Every routed model must also be in the allowed list")
    firm.model_policy = {"allowed": body.allowed, "routes": dict(body.routes)}
    await audit.record(db, p, "firm.model_policy_update", "firm", firm.id, None, firm.model_policy)
    return await models(db, p)


# --------------------------------------------------------------------------- ask


@dataclass
class Prepared:
    principal: Principal
    thread_id: uuid.UUID
    matter_id: uuid.UUID
    question_id: uuid.UUID
    answer_id: uuid.UUID
    mode: str
    task: TaskClass
    model: str
    policy: dict[str, Any]
    sources: list[Source]
    messages: list[Message]
    terms: list[str]


async def _history(db: AsyncSession, thread_id: uuid.UUID, turns: int) -> list[tuple[str, str]]:
    rows = (
        await db.scalars(
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id, ThreadMessage.status == "complete")
            .order_by(ThreadMessage.created_at.desc())
            .limit(turns * 2)
        )
    ).all()
    pairs: list[tuple[str, str]] = []
    pending_answer: str | None = None
    for m in rows:  # newest first: an answer, then the question before it
        if m.role == "assistant":
            pending_answer = m.content[:800]
        elif pending_answer is not None:
            pairs.append((m.content[:600], pending_answer))
            pending_answer = None
    return list(reversed(pairs))


async def prepare_ask(db: AsyncSession, p: Principal, thread_id: uuid.UUID, body: AskRequest) -> Prepared:
    thread = await _thread(db, p, thread_id)
    s = get_settings()
    doc_ids = list(dict.fromkeys(body.document_ids or []))
    if doc_ids:
        found = set(
            (
                await db.scalars(
                    select(Document.id).where(
                        Document.id.in_(doc_ids),
                        Document.matter_id == thread.matter_id,
                        Document.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if len(found) != len(doc_ids):
            raise NotFound("One or more chosen documents are not in this matter")
    firm = await db.get(Firm, p.firm_id)
    policy = firm.model_policy if firm else {}
    task = TASK_FOR_MODE[body.mode]
    try:
        model = _gateway(policy).policy.resolve(task, body.model)
    except ModelNotAllowed as e:
        raise Unprocessable(str(e)) from e

    history = await _history(db, thread_id, s.assistant_history_turns)
    sources, terms = await retrieve(db, thread.matter_id, body.content, doc_ids, body.mode)
    messages = build_messages(body.mode, body.content, sources, history, body.draft_kind)
    options = {
        "draft_kind": body.draft_kind,
        "document_ids": [str(d) for d in doc_ids],
        "requested_model": body.model,
    }
    question = ThreadMessage(
        firm_id=p.firm_id,
        matter_id=thread.matter_id,
        thread_id=thread.id,
        role="user",
        content=body.content.strip(),
        mode=body.mode,
        options=options,
        created_by=p.user_id,
    )
    answer = ThreadMessage(
        firm_id=p.firm_id,
        matter_id=thread.matter_id,
        thread_id=thread.id,
        role="assistant",
        content="",
        mode=body.mode,
        options=options,
        sources=[src.public() for src in sources],
        status="streaming",
        model=model if sources else None,
        created_by=p.user_id,
    )
    db.add(question)
    await db.flush()  # question first, so it sorts before the answer
    db.add(answer)
    if thread.title == NEW_THREAD:
        thread.title = body.content.strip().splitlines()[0][:80]
    thread.updated_at = func.now()
    await db.flush()
    await audit.record(
        db,
        p,
        "assistant.ask",
        "thread",
        thread.id,
        thread.matter_id,
        {
            "message_id": str(answer.id),
            "mode": body.mode,
            "chars": len(body.content),
            "documents": len(doc_ids),
            "chunks": [str(src.chunk_id) for src in sources],
        },
    )
    return Prepared(
        principal=p,
        thread_id=thread.id,
        matter_id=thread.matter_id,
        question_id=question.id,
        answer_id=answer.id,
        mode=body.mode,
        task=task,
        model=model,
        policy=policy,
        sources=sources,
        messages=messages,
        terms=terms,
    )


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def _prompt_hash(messages: list[Message]) -> str:
    h = hashlib.sha256()
    for m in messages:
        h.update(m.role.encode() + b"\x00" + m.content.encode() + b"\x00")
    return h.hexdigest()


async def _model_verify(
    prep: Prepared, sentences: list[Sentence], by_key: dict[str, str]
) -> tuple[int, int, float, str | None]:
    """Optional second pass (assistant_verifier = "model"). Returns tokens in/out, cost, error."""
    items = [(i, s.text, "\n".join(by_key[k] for k in s.cites)) for i, s in enumerate(sentences) if s.cites]
    if not items:
        return 0, 0, 0.0, None
    try:
        c = await gateway_for(prep.policy).complete(
            "extract", verifier_messages(items), max_tokens=1200, json_schema=VERIFY_SCHEMA
        )
        verdicts = json.loads(c.text).get("sentences", [])
        merge_model_labels(
            sentences, {int(v["index"]): (str(v["label"]), str(v.get("reason", ""))) for v in verdicts}
        )
        return c.usage.input_tokens, c.usage.output_tokens, c.cost_usd, None
    except (LLMError, ValueError, KeyError, TypeError) as e:
        return 0, 0, 0.0, f"Model verifier skipped: {e}"


async def _finish(prep: Prepared, values: dict[str, Any], audit_detail: dict[str, Any] | None) -> MessageOut:
    async with tenant_session(prep.principal.firm_id) as db:
        msg = await db.get(ThreadMessage, prep.answer_id)
        assert msg is not None
        for k, v in values.items():
            setattr(msg, k, v)
        thread = await db.get(Thread, prep.thread_id)
        if thread is not None:
            thread.updated_at = func.now()
        if audit_detail is not None:
            await audit.record(
                db, prep.principal, "ai.call", "thread_message", msg.id, prep.matter_id, audit_detail
            )
        await db.flush()
        await db.refresh(msg)
        return _message_out(msg)


async def run_ask(prep: Prepared) -> AsyncIterator[str]:
    """SSE stream: meta → (notice | delta)* → done, or error. Always leaves the answer row final."""
    finished = False
    yield sse(
        "meta",
        {
            "thread_id": prep.thread_id,
            "question_id": prep.question_id,
            "message_id": prep.answer_id,
            "mode": prep.mode,
            "model": prep.model if prep.sources else None,
            "sources": [s.public() for s in prep.sources],
            "terms": prep.terms,
        },
    )
    try:
        if not prep.sources:  # nothing to ground an answer on: say so without calling a model
            reply = NO_SOURCES[prep.mode]
            yield sse("delta", {"text": reply})
            sentences = parse_answer(reply)
            for sentence in sentences:  # system text, not a claim about the documents
                sentence.label = "uncited"
            summary = {**verify_counts(sentences), "verifier": "none"}
            out = await _finish(
                prep,
                {
                    "content": reply,
                    "sentences": [s.as_dict() for s in sentences],
                    "verification": summary,
                    "status": "complete",
                    "model": None,
                },
                None,
            )
            finished = True
            yield sse("done", out.model_dump(mode="json"))
            return

        buf: list[str] = []
        done: Done | None = None
        try:
            async for item in gateway_for(prep.policy).stream(prep.task, prep.messages, model=prep.model):
                if isinstance(item, TextDelta):
                    buf.append(item.text)
                    yield sse("delta", {"text": item.text})
                elif isinstance(item, Notice):
                    yield sse("notice", {"text": item.text})
                elif isinstance(item, Done):
                    done = item
        except LLMError as e:
            out = await _finish(
                prep,
                {"content": strip_markers("".join(buf)), "status": "failed", "error": str(e)},
                {"task": prep.task, "model": prep.model, "error": str(e), "status": e.status},
            )
            finished = True
            yield sse(
                "error",
                {
                    "detail": str(e),
                    "status": e.status,
                    "retry_after": e.retry_after,
                    "message": out.model_dump(mode="json"),
                },
            )
            return

        answer = "".join(buf).strip()
        sentences = parse_answer(answer)
        by_key = {s.key: s.text for s in prep.sources}
        summary = verify(sentences, by_key, prep.mode)
        v_in = v_out = 0
        v_cost = 0.0
        if get_settings().assistant_verifier == "model":
            v_in, v_out, v_cost, v_err = await _model_verify(prep, sentences, by_key)
            summary = {**verify_counts(sentences), "verifier": "lexical+model"}
            if v_err:
                summary["note"] = v_err
        usage = done.usage if done else None
        values = {
            "content": strip_markers(answer),
            "sentences": [s.as_dict() for s in sentences],
            "verification": summary,
            "status": "complete",
            "model": done.model if done else prep.model,
            "input_tokens": (usage.input_tokens if usage else 0) + v_in,
            "output_tokens": (usage.output_tokens if usage else 0) + v_out,
            "latency_ms": done.latency_ms if done else None,
            "cost_usd": round((done.cost_usd if done else 0.0) + v_cost, 6),
        }
        out = await _finish(
            prep,
            values,
            {
                "task": prep.task,
                "model": values["model"],
                "prompt_sha256": _prompt_hash(prep.messages),
                "chunks": [str(s.chunk_id) for s in prep.sources],
                "input_tokens": values["input_tokens"],
                "output_tokens": values["output_tokens"],
                "latency_ms": values["latency_ms"],
                "cost_usd": values["cost_usd"],
                "verification": summary,
            },
        )
        finished = True
        yield sse("done", out.model_dump(mode="json"))
    finally:
        if not finished:  # client went away or something unexpected: never leave a "streaming" row
            try:
                await asyncio.shield(
                    _finish(prep, {"status": "failed", "error": "The answer was interrupted."}, None)
                )
            except Exception:  # best effort while unwinding: never mask the original problem
                log.exception("Could not mark answer %s as interrupted", prep.answer_id)


def verify_counts(sentences: list[Sentence]) -> dict[str, int]:
    labels = [s.label for s in sentences]
    return {k: labels.count(k) for k in ("supported", "partial", "unsupported", "uncited")}


# --------------------------------------------------------------------------- export


async def export_docx(
    db: AsyncSession, p: Principal, thread_id: uuid.UUID, message_id: uuid.UUID
) -> tuple[bytes, str]:
    thread = await _thread(db, p, thread_id)
    msg = await db.get(ThreadMessage, message_id)
    if msg is None or msg.thread_id != thread.id or msg.role != "assistant" or msg.status != "complete":
        raise NotFound("Answer not found")
    question = await db.scalar(
        select(ThreadMessage)
        .where(
            ThreadMessage.thread_id == thread.id,
            ThreadMessage.role == "user",
            ThreadMessage.created_at < msg.created_at,
        )
        .order_by(ThreadMessage.created_at.desc())
        .limit(1)
    )
    numbers = {src["key"]: i + 1 for i, src in enumerate(msg.sources)}
    blocks: dict[int, list[tuple[str, str]]] = {}
    for s in msg.sentences:
        cites = "".join(f"[{numbers[k]}]" for k in s.get("cites", []) if k in numbers)
        blocks.setdefault(int(s.get("block", 0)), []).append(
            ((s.get("prefix") or "") + str(s["text"]), cites)
        )
    sources = []
    for src in msg.sources:
        pages = (
            src["page_from"]
            if src["page_from"] == src["page_to"]
            else f"{src['page_from']}\u2013{src['page_to']}"
        )
        section = f", {src['section_path']}" if src.get("section_path") else ""
        sources.append(f"[{numbers[src['key']]}] {src['document_title']}, p. {pages}{section}")
    v = msg.verification or {}
    note = (
        f"Generated by Dataviv AI OS{f' with {msg.model}' if msg.model else ''} on "
        f"{msg.created_at:%d %b %Y}. Verification: {v.get('supported', 0)} supported, {v.get('partial', 0)} partly "
        f"supported, {v.get('unsupported', 0)} unsupported. Check every citation before relying on this."
    )
    kind = (msg.options or {}).get("draft_kind")
    heading = {"ask": "Answer", "summarise": "Summary", "draft": f"Draft {kind or ''}".strip()}[msg.mode]
    data = build_docx(
        f"{heading}: {thread.title}",
        f"Question: {question.content if question else ''}"[:500],
        [blocks[k] for k in sorted(blocks)],
        sources,
        note,
    )
    await audit.record(
        db, p, "assistant.export", "thread_message", msg.id, thread.matter_id, {"format": "docx"}
    )
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in thread.title)[:60].strip() or "answer"
    return data, f"{safe}.docx"
