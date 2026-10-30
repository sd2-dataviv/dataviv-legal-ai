"""Drafts: editable, versioned, approvable documents shared by a matter's members (DRF-01..07).

Who may do what (matter roles; walls and other firms see nothing — 404, ADR-007):

| Action                              | viewer | member | partner / lead |
|-------------------------------------|:------:|:------:|:--------------:|
| read, compare versions, comment     |   ✓    |   ✓    |       ✓        |
| create, edit, restore, ask approval |        |   ✓    |       ✓        |
| approve / request changes           |        |        | ✓ (if chosen, and not their own request) |

Every save makes a new immutable version (ADR-014). A save names the version it started from;
if someone saved in between, the API answers 409 and the editor may compare, then save on top.
While a draft waits for approval it is locked; approving records the exact version approved.
"""

import difflib
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.models import AuditEvent, User
from app.core.permissions import (
    EDIT_ROLES,
    MANAGER_ROLES,
    accessible_matter_ids,
    require_matter_access,
    user_matter_role,
)
from app.core.security import Principal
from app.db.session import after_commit, after_rollback
from app.modules.assistant.docx import build_docx
from app.modules.assistant.models import Thread, ThreadMessage
from app.modules.drafts import live
from app.modules.drafts.models import (
    Draft,
    DraftComment,
    DraftPresence,
    DraftReview,
    DraftSuggestion,
    DraftVersion,
)
from app.modules.drafts.schemas import (
    ActivityOut,
    CommentCreate,
    CommentOut,
    DiffOp,
    DiffOut,
    DraftCreate,
    DraftDetail,
    DraftSummary,
    DraftUpdate,
    Head,
    Kind,
    LiveInfo,
    LiveSave,
    LiveText,
    Person,
    PresenceOut,
    ReviewDecision,
    ReviewOut,
    ReviewRequest,
    Status,
    SuggestionOut,
    VersionContent,
    VersionCreate,
    VersionOut,
)

PRESENCE_WINDOW = timedelta(seconds=30)
KIND_TITLE = {
    "notice": "Legal notice",
    "reply": "Reply to notice",
    "email": "Client email",
    "memo": "Internal memo",
}


# --------------------------------------------------------------------------- helpers


async def _draft(
    db: AsyncSession, p: Principal, draft_id: uuid.UUID, *, lock: bool = False
) -> tuple[Draft, str]:
    stmt = select(Draft).where(Draft.id == draft_id)
    if lock:
        stmt = stmt.with_for_update()
    draft = await db.scalar(stmt)
    if draft is None:
        raise NotFound("Draft not found")
    try:
        role = await require_matter_access(db, p, draft.matter_id)
    except NotFound:
        raise NotFound("Draft not found") from None  # same answer as a draft that does not exist
    return draft, role


draft_for_live = _draft  # the live socket checks access exactly like every other draft endpoint


def _require_editor(role: str) -> None:
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter can read and comment on drafts, but not edit them")


class _Names:
    """Resolve user ids to names in one query per request."""

    def __init__(self, names: dict[uuid.UUID, str]) -> None:
        self._names = names

    @classmethod
    async def load(cls, db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> "_Names":
        wanted = {i for i in ids if i is not None}
        rows = (
            (await db.execute(select(User.id, User.name).where(User.id.in_(wanted)))).all() if wanted else []
        )
        return cls({r[0]: r[1] for r in rows})

    def __call__(self, uid: uuid.UUID) -> Person:
        return Person(id=uid, name=self._names.get(uid, "Unknown user"))

    def opt(self, uid: uuid.UUID | None) -> Person | None:
        return self(uid) if uid else None


async def _pending_review(db: AsyncSession, draft_id: uuid.UUID) -> DraftReview | None:
    return await db.scalar(
        select(DraftReview).where(DraftReview.draft_id == draft_id, DraftReview.status == "pending")
    )


async def _version(db: AsyncSession, draft: Draft, version_no: int) -> DraftVersion:
    v = await db.scalar(
        select(DraftVersion).where(DraftVersion.draft_id == draft.id, DraftVersion.version_no == version_no)
    )
    if v is None:
        raise NotFound(f"Version {version_no} not found")
    return v


def _touch(draft: Draft, p: Principal) -> None:
    draft.updated_by = p.user_id
    draft.updated_at = func.now()


async def _new_version(
    db: AsyncSession,
    p: Principal,
    draft: Draft,
    content: str,
    note: str | None,
    contributors: Iterable[uuid.UUID] | None = None,
) -> DraftVersion:
    n = draft.current_version_no + 1
    version = DraftVersion(
        firm_id=p.firm_id,
        matter_id=draft.matter_id,
        draft_id=draft.id,
        version_no=n,
        content=content,
        note=note,
        created_by=p.user_id,
        contributors=sorted(set(contributors or ()) or {p.user_id}),
    )
    db.add(version)
    draft.current_version_no = n
    if draft.status == "approved":
        draft.status = "draft"  # edited after approval: the approved version stays on record
    _touch(draft, p)
    await db.flush()
    await db.refresh(version)
    return version


def _version_out(v: DraftVersion, names: _Names) -> VersionOut:
    return VersionOut(
        version_no=v.version_no,
        note=v.note,
        created_by=names(v.created_by),
        created_at=v.created_at,
        chars=len(v.content),
        contributors=[names(u) for u in (v.contributors or [])],
    )


async def _sync_live(db: AsyncSession, draft: Draft, content: str) -> None:
    """A version was made from outside the live editor (save, restore): the working copy follows it."""
    room = await live.room_for(db, draft)
    if room is not None:
        await room.replace_all(content, set())
        room.contributors.clear()
        room.schedule_persist()


def _changed(db: AsyncSession, draft: Draft, p: Principal, what: str, **extra: Any) -> None:
    """After commit, tell everyone with the draft open what changed, so their screens refresh."""
    live.notify(db, draft.id, {"type": "changed", "what": what, "by": str(p.user_id), **extra})


async def _audit(db: AsyncSession, p: Principal, action: str, draft: Draft, detail: dict[str, Any]) -> None:
    await audit.record(db, p, action, "draft", draft.id, draft.matter_id, detail)


# --------------------------------------------------------------------------- create and list


def content_with_sources(
    sentences: list[dict[str, Any]], sources: list[dict[str, Any]], fallback: str
) -> tuple[str, list[dict[str, Any]]]:
    """Cited sentences ([S1] keys) → editable text with [1]-style citations, plus the numbered sources.

    Used for Assistant answers and for workflow output documents."""
    numbers = {src["key"]: i + 1 for i, src in enumerate(sources)}
    blocks: dict[int, list[str]] = {}
    for s in sentences:
        cites = "".join(f"[{numbers[k]}]" for k in s.get("cites", []) if k in numbers)
        text = str(s["text"]) + (f" {cites}" if cites else "")
        block = int(s.get("block", 0))
        if block in blocks:
            blocks[block].append(text)
        else:
            blocks[block] = [str(s.get("prefix") or "") + text]  # keep "1. " / "- " list markers
    content = "\n\n".join(" ".join(blocks[k]) for k in sorted(blocks)) or fallback
    numbered = [
        {
            "n": numbers[src["key"]],
            "chunk_id": src["chunk_id"],
            "document_id": src["document_id"],
            "document_title": src["document_title"],
            "section_path": src.get("section_path"),
            "page_from": src["page_from"],
            "page_to": src["page_to"],
        }
        for src in sources
    ]
    return content, numbered


def _content_from_answer(msg: ThreadMessage) -> tuple[str, list[dict[str, Any]]]:
    """An Assistant answer → editable text with [1]-style citations, plus the numbered sources."""
    return content_with_sources(msg.sentences, msg.sources, msg.content)


async def create_draft(
    db: AsyncSession, p: Principal, matter_id: uuid.UUID, body: DraftCreate
) -> DraftDetail:
    role = await require_matter_access(db, p, matter_id)
    _require_editor(role)
    sources: list[dict[str, Any]] = []
    content, kind, title, note = body.content, body.kind, body.title, "Created"
    source_message_id = None
    if body.from_message_id:
        msg = await db.get(ThreadMessage, body.from_message_id)
        if msg is None or msg.matter_id != matter_id or msg.role != "assistant" or msg.status != "complete":
            raise NotFound("That answer is not in this matter")
        content, sources = _content_from_answer(msg)
        kind = (msg.options or {}).get("draft_kind") or body.kind
        if not title:
            thread = await db.get(Thread, msg.thread_id)
            title = f"{KIND_TITLE.get(kind, 'Draft')}: {thread.title}" if thread else None
        note, source_message_id = "Created from an Assistant answer", msg.id
    draft = Draft(
        firm_id=p.firm_id,
        matter_id=matter_id,
        title=(title or "Untitled draft").strip()[:200],
        kind=kind,
        status="draft",
        current_version_no=1,
        sources=sources,
        source_message_id=source_message_id,
        created_by=p.user_id,
        updated_by=p.user_id,
    )
    db.add(draft)
    await db.flush()
    db.add(
        DraftVersion(
            firm_id=p.firm_id,
            matter_id=matter_id,
            draft_id=draft.id,
            version_no=1,
            content=content,
            note=note,
            created_by=p.user_id,
        )
    )
    await db.flush()
    await _audit(
        db,
        p,
        "draft.create",
        draft,
        {
            "title": draft.title,
            "kind": kind,
            "from_message": str(source_message_id) if source_message_id else None,
        },
    )
    return await detail(db, p, draft.id)


async def _summaries(db: AsyncSession, drafts: list[Draft]) -> list[DraftSummary]:
    if not drafts:
        return []
    pending = {
        r.draft_id: r
        for r in (
            await db.scalars(
                select(DraftReview).where(
                    DraftReview.draft_id.in_([d.id for d in drafts]), DraftReview.status == "pending"
                )
            )
        ).all()
    }
    names = await _Names.load(db, [d.updated_by for d in drafts] + [r.reviewer_id for r in pending.values()])
    return [
        DraftSummary(
            id=d.id,
            matter_id=d.matter_id,
            title=d.title,
            kind=cast(Kind, d.kind),
            status=cast(Status, d.status),
            current_version_no=d.current_version_no,
            approved_version_no=d.approved_version_no,
            updated_by=names(d.updated_by),
            updated_at=d.updated_at,
            reviewer=names(pending[d.id].reviewer_id) if d.id in pending else None,
        )
        for d in drafts
    ]


async def list_drafts(db: AsyncSession, p: Principal, matter_id: uuid.UUID) -> list[DraftSummary]:
    await require_matter_access(db, p, matter_id)
    drafts = (
        await db.scalars(select(Draft).where(Draft.matter_id == matter_id).order_by(Draft.updated_at.desc()))
    ).all()
    return await _summaries(db, list(drafts))


async def awaiting_me(db: AsyncSession, p: Principal) -> list[DraftSummary]:
    """Drafts waiting for *my* approval, across every matter I can still open."""
    allowed = await accessible_matter_ids(db, p)
    if not allowed:
        return []
    drafts = (
        await db.scalars(
            select(Draft)
            .join(DraftReview, (DraftReview.draft_id == Draft.id) & (DraftReview.status == "pending"))
            .where(DraftReview.reviewer_id == p.user_id, Draft.matter_id.in_(allowed))
            .order_by(DraftReview.created_at)
        )
    ).all()
    return await _summaries(db, list(drafts))


# --------------------------------------------------------------------------- read


async def _present(db: AsyncSession, draft_id: uuid.UUID) -> list[DraftPresence]:
    since = datetime.now(UTC) - PRESENCE_WINDOW
    return list(
        (
            await db.scalars(
                select(DraftPresence).where(
                    DraftPresence.draft_id == draft_id, DraftPresence.seen_at >= since
                )
            )
        ).all()
    )


async def detail(db: AsyncSession, p: Principal, draft_id: uuid.UUID) -> DraftDetail:
    draft, role = await _draft(db, p, draft_id)
    versions = (
        await db.scalars(
            select(DraftVersion)
            .where(DraftVersion.draft_id == draft.id)
            .order_by(DraftVersion.version_no.desc())
        )
    ).all()
    current = next(v for v in versions if v.version_no == draft.current_version_no)
    reviews = (
        await db.scalars(
            select(DraftReview)
            .where(DraftReview.draft_id == draft.id)
            .order_by(DraftReview.created_at.desc())
        )
    ).all()
    comments = (
        await db.scalars(
            select(DraftComment).where(DraftComment.draft_id == draft.id).order_by(DraftComment.created_at)
        )
    ).all()
    present = await _present(db, draft.id)
    events = (
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.object_type == "draft", AuditEvent.object_id == str(draft.id))
            .order_by(AuditEvent.id.desc())
            .limit(50)
        )
    ).all()
    suggestions = (
        await db.scalars(
            select(DraftSuggestion)
            .where(DraftSuggestion.draft_id == draft.id)
            .order_by(DraftSuggestion.created_at.desc())
            .limit(30)
        )
    ).all()
    room = await live.room_for(db, draft)
    live_contributors = set(room.contributors) if room else set()
    names = await _Names.load(
        db,
        {draft.created_by, draft.updated_by, draft.approved_by}
        | {v.created_by for v in versions}
        | {u for v in versions for u in (v.contributors or [])}
        | {x for r in reviews for x in (r.requested_by, r.reviewer_id, r.decided_by)}
        | {x for c in comments for x in (c.author_id, c.resolved_by)}
        | {x.user_id for x in present}
        | {e.actor_id for e in events}
        | {x for s in suggestions for x in (s.requested_by, s.decided_by)}
        | live_contributors,
    )
    pending = next((r for r in reviews if r.status == "pending"), None)
    return DraftDetail(
        id=draft.id,
        matter_id=draft.matter_id,
        title=draft.title,
        kind=cast(Kind, draft.kind),
        status=cast(Status, draft.status),
        current_version_no=draft.current_version_no,
        approved_version_no=draft.approved_version_no,
        updated_by=names(draft.updated_by),
        updated_at=draft.updated_at,
        reviewer=names(pending.reviewer_id) if pending else None,
        content=current.content,
        sources=draft.sources,
        approved_by=names.opt(draft.approved_by),
        approved_at=draft.approved_at,
        created_by=names(draft.created_by),
        created_at=draft.created_at,
        my_matter_role=role,
        can_edit=role in EDIT_ROLES and draft.status != "in_review",
        versions=[_version_out(v, names) for v in versions],
        reviews=[
            ReviewOut(
                id=r.id,
                version_no=r.version_no,
                status=cast(Any, r.status),
                requested_by=names(r.requested_by),
                reviewer=names(r.reviewer_id),
                message=r.message,
                decision_note=r.decision_note,
                decided_at=r.decided_at,
                created_at=r.created_at,
            )
            for r in reviews
        ],
        comments=[
            CommentOut(
                id=c.id,
                version_no=c.version_no,
                author=names(c.author_id),
                body=c.body,
                quote=c.quote,
                resolved_by=names.opt(c.resolved_by),
                resolved_at=c.resolved_at,
                created_at=c.created_at,
            )
            for c in comments
        ],
        present=[PresenceOut(user=names(x.user_id), editing=x.editing, seen_at=x.seen_at) for x in present],
        activity=[
            ActivityOut(action=e.action, actor=names.opt(e.actor_id), detail=e.detail or {}, at=e.created_at)
            for e in events
        ],
        live=LiveInfo(
            available=room is not None,
            unsaved_changes=room is not None and room.value() != current.content,
            contributors=[names(u) for u in sorted(live_contributors)],
        ),
        suggestions=[suggestion_out(s, names) for s in suggestions],
        signatures=await _signatures(db, draft.id),
    )


async def _signatures(db: AsyncSession, draft_id: uuid.UUID) -> list[Any]:
    from app.modules.esign.service import for_draft  # the e-sign module builds on this one

    return await for_draft(db, draft_id)


def suggestion_out(s: DraftSuggestion, names: "_Names") -> SuggestionOut:
    ops, _, _ = diff_ops(s.quote, s.replacement)
    return SuggestionOut(
        id=s.id,
        requested_by=names(s.requested_by),
        instruction=s.instruction,
        scope=cast(Any, s.scope),
        quote=s.quote,
        replacement=s.replacement,
        explanation=s.explanation,
        diff=ops,
        sources=s.sources or [],
        warnings=list(s.warnings or []),
        status=cast(Any, s.status),
        decided_by=names.opt(s.decided_by),
        decided_at=s.decided_at,
        model=s.model,
        cost_usd=float(s.cost_usd or 0),
        created_at=s.created_at,
    )


async def get_version(db: AsyncSession, p: Principal, draft_id: uuid.UUID, version_no: int) -> VersionContent:
    draft, _ = await _draft(db, p, draft_id)
    v = await _version(db, draft, version_no)
    names = await _Names.load(db, {v.created_by, *(v.contributors or [])})
    return VersionContent(**_version_out(v, names).model_dump(), content=v.content)


_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def diff_ops(a: str, b: str) -> tuple[list[DiffOp], int, int]:
    """Word-level differences between two texts (whitespace and punctuation kept as tokens)."""
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    ops: list[DiffOp] = []
    ins = dels = 0

    def push(op: Literal["equal", "insert", "delete"], text: str) -> None:
        if not text:
            return
        if ops and ops[-1].op == op:
            ops[-1].text += text
        else:
            ops.append(DiffOp(op=op, text=text))

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if tag == "equal":
            push("equal", "".join(ta[i1:i2]))
            continue
        if tag in ("replace", "delete"):
            push("delete", "".join(ta[i1:i2]))
            dels += sum(1 for t in ta[i1:i2] if t[0].isalnum() or t[0] == "_")
        if tag in ("replace", "insert"):
            push("insert", "".join(tb[j1:j2]))
            ins += sum(1 for t in tb[j1:j2] if t[0].isalnum() or t[0] == "_")
    return ops, ins, dels


async def diff(db: AsyncSession, p: Principal, draft_id: uuid.UUID, from_no: int, to_no: int) -> DiffOut:
    draft, _ = await _draft(db, p, draft_id)
    a, b = await _version(db, draft, from_no), await _version(db, draft, to_no)
    ops, ins, dels = diff_ops(a.content, b.content)
    return DiffOut(from_version=from_no, to_version=to_no, ops=ops, inserted_words=ins, deleted_words=dels)


# --------------------------------------------------------------------------- edit


def _unlocked(draft: Draft) -> None:
    if draft.status == "in_review":
        raise Conflict("This draft is waiting for approval. Withdraw the request to edit it.")


async def update_meta(db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: DraftUpdate) -> DraftDetail:
    draft, role = await _draft(db, p, draft_id, lock=True)
    _require_editor(role)
    _unlocked(draft)
    changes: dict[str, Any] = {}
    if body.title is not None and body.title.strip() != draft.title:
        changes["title"] = draft.title = body.title.strip()
    if body.kind is not None and body.kind != draft.kind:
        changes["kind"] = draft.kind = body.kind
    if changes:
        _touch(draft, p)
        await db.flush()
        await _audit(db, p, "draft.update", draft, changes)
        _changed(db, draft, p, "details")
    return await detail(db, p, draft_id)


async def save_version(
    db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: VersionCreate
) -> VersionOut:
    draft, role = await _draft(db, p, draft_id, lock=True)  # row lock: two saves can never take one number
    _require_editor(role)
    _unlocked(draft)
    if body.base_version_no > draft.current_version_no:
        raise Unprocessable(f"Version {body.base_version_no} does not exist yet")
    if body.base_version_no != draft.current_version_no and not body.force:
        names = await _Names.load(db, {draft.updated_by})
        raise Conflict(
            f"{names(draft.updated_by).name} saved version {draft.current_version_no} while you were editing "
            f"version {body.base_version_no}. Compare the changes, then save yours on top or reload. "
            "Every version is kept."
        )
    current = await _version(db, draft, draft.current_version_no)
    if current.content == body.content:
        raise Unprocessable(f"Nothing has changed since version {draft.current_version_no}")
    forced = body.base_version_no != draft.current_version_no
    version = await _new_version(db, p, draft, body.content, (body.note or "").strip() or None)
    await _audit(
        db,
        p,
        "draft.save",
        draft,
        {
            "version_no": version.version_no,
            "base": body.base_version_no,
            "saved_on_top": forced,
            "chars": len(body.content),
        },
    )
    await _sync_live(db, draft, body.content)
    _changed(db, draft, p, "version", version_no=version.version_no)
    names = await _Names.load(db, {p.user_id})
    return _version_out(version, names)


async def save_live(db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: LiveSave) -> VersionOut:
    """Turn the shared working copy into the next version, crediting everyone who changed it."""
    draft, role = await _draft(db, p, draft_id, lock=True)
    _require_editor(role)
    _unlocked(draft)
    room = await live.room_for(db, draft)
    if room is None:
        raise Unprocessable("Live editing is not available on this server; save a version instead")
    text = room.value()
    current = await _version(db, draft, draft.current_version_no)
    if text == current.content:
        raise Unprocessable(f"Nothing has changed since version {draft.current_version_no}")
    contributors = set(room.contributors) or {p.user_id}
    version = await _new_version(db, p, draft, text, (body.note or "").strip() or None, contributors)
    saved = set(room.contributors)
    room.contributors.clear()
    after_rollback(db, lambda: room.contributors.update(saved))
    room.schedule_persist()
    await _audit(
        db,
        p,
        "draft.save",
        draft,
        {
            "version_no": version.version_no,
            "live": True,
            "contributors": [str(u) for u in sorted(contributors)],
            "chars": len(text),
        },
    )
    _changed(db, draft, p, "version", version_no=version.version_no)
    names = await _Names.load(db, {p.user_id, *contributors})
    return _version_out(version, names)


async def live_text(db: AsyncSession, p: Principal, draft_id: uuid.UUID) -> LiveText:
    """The working copy as it is now (for readers without the live editor, and for tests)."""
    draft, _ = await _draft(db, p, draft_id)
    current = await _version(db, draft, draft.current_version_no)
    room = await live.room_for(db, draft)
    text = room.value() if room is not None else current.content
    return LiveText(
        text=text, unsaved_changes=text != current.content, current_version_no=draft.current_version_no
    )


async def restore(db: AsyncSession, p: Principal, draft_id: uuid.UUID, version_no: int) -> VersionOut:
    draft, role = await _draft(db, p, draft_id, lock=True)
    _require_editor(role)
    _unlocked(draft)
    old = await _version(db, draft, version_no)
    if version_no == draft.current_version_no:
        raise Unprocessable("That is already the current version")
    version = await _new_version(db, p, draft, old.content, f"Restored from version {version_no}")
    await _audit(
        db, p, "draft.restore", draft, {"from_version": version_no, "version_no": version.version_no}
    )
    await _sync_live(db, draft, old.content)
    _changed(db, draft, p, "version", version_no=version.version_no)
    return _version_out(version, await _Names.load(db, {p.user_id}))


# --------------------------------------------------------------------------- approval


async def request_review(
    db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: ReviewRequest
) -> DraftDetail:
    draft, role = await _draft(db, p, draft_id, lock=True)
    _require_editor(role)
    if draft.status == "in_review":
        raise Conflict("This draft is already waiting for approval")
    if body.reviewer_id == p.user_id:
        raise Unprocessable("Choose someone else to approve your draft")
    if await user_matter_role(db, body.reviewer_id, draft.matter_id) not in MANAGER_ROLES:
        raise Unprocessable("The approver must be the matter lead or a partner on this matter")
    room = await live.room_for(db, draft)
    if room is not None:
        # Freeze the shared copy first, so the version sent for approval is exactly what everyone sees.
        was_locked = room.locked
        room.locked = True
        after_rollback(db, lambda: setattr(room, "locked", was_locked))
        current = await _version(db, draft, draft.current_version_no)
        if room.value() != current.content:
            contributors = set(room.contributors) or {p.user_id}
            await _new_version(db, p, draft, room.value(), "Saved for approval", contributors)
            saved = set(room.contributors)
            room.contributors.clear()
            after_rollback(db, lambda: room.contributors.update(saved))
            room.schedule_persist()
    if draft.status == "approved" and draft.approved_version_no == draft.current_version_no:
        raise Conflict(f"Version {draft.current_version_no} is already approved")
    db.add(
        DraftReview(
            firm_id=p.firm_id,
            matter_id=draft.matter_id,
            draft_id=draft.id,
            version_no=draft.current_version_no,
            requested_by=p.user_id,
            reviewer_id=body.reviewer_id,
            message=(body.message or "").strip() or None,
        )
    )
    draft.status = "in_review"
    _touch(draft, p)
    await db.flush()
    await _audit(
        db,
        p,
        "draft.review_request",
        draft,
        {"version_no": draft.current_version_no, "reviewer_id": str(body.reviewer_id)},
    )
    _lock_live(db, draft, locked=True, status="in_review")
    _changed(db, draft, p, "review")
    return await detail(db, p, draft_id)


def _lock_live(db: AsyncSession, draft: Draft, *, locked: bool, status: str) -> None:
    """Lock or unlock the shared copy for everyone, once the decision is committed."""

    async def apply() -> None:
        room = live.loaded(draft.id)
        if room is not None:
            await room.set_locked(locked, status)

    after_commit(db, apply)


async def decide(db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: ReviewDecision) -> DraftDetail:
    draft, role = await _draft(db, p, draft_id, lock=True)
    review = await _pending_review(db, draft.id)
    if review is None:
        raise Conflict("Nothing is waiting for approval on this draft")
    if review.reviewer_id != p.user_id:
        names = await _Names.load(db, {review.reviewer_id})
        raise Forbidden(f"Only {names(review.reviewer_id).name} can decide on this request")
    if role not in MANAGER_ROLES:
        raise Forbidden("You are no longer the lead or a partner on this matter")
    note = (body.note or "").strip() or None
    if body.decision == "request_changes" and not note:
        raise Unprocessable("Say what needs to change")
    now = datetime.now(UTC)
    review.decided_by, review.decided_at, review.decision_note = p.user_id, now, note
    if body.decision == "approve":
        review.status = draft.status = "approved"
        draft.approved_version_no, draft.approved_by, draft.approved_at = review.version_no, p.user_id, now
    else:
        review.status = draft.status = "changes_requested"
    _touch(draft, p)
    await db.flush()
    await _audit(
        db,
        p,
        "draft.approve" if body.decision == "approve" else "draft.changes_requested",
        draft,
        {"version_no": review.version_no, "note": note},
    )
    _lock_live(db, draft, locked=False, status=draft.status)
    _changed(db, draft, p, "review")
    return await detail(db, p, draft_id)


async def withdraw(db: AsyncSession, p: Principal, draft_id: uuid.UUID) -> DraftDetail:
    draft, _ = await _draft(db, p, draft_id, lock=True)
    review = await _pending_review(db, draft.id)
    if review is None:
        raise Conflict("Nothing is waiting for approval on this draft")
    if p.user_id not in (review.requested_by, review.reviewer_id):
        raise Forbidden("Only the person who asked for approval, or the approver, can withdraw it")
    review.status = "withdrawn"
    review.decided_by, review.decided_at = p.user_id, datetime.now(UTC)
    draft.status = "approved" if draft.approved_version_no == draft.current_version_no else "draft"
    _touch(draft, p)
    await db.flush()
    await _audit(db, p, "draft.review_withdrawn", draft, {"version_no": review.version_no})
    _lock_live(db, draft, locked=False, status=draft.status)
    _changed(db, draft, p, "review")
    return await detail(db, p, draft_id)


# --------------------------------------------------------------------------- comments and presence


async def add_comment(
    db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: CommentCreate
) -> DraftDetail:
    draft, _ = await _draft(db, p, draft_id)  # every member, viewers included, may comment
    db.add(
        DraftComment(
            firm_id=p.firm_id,
            matter_id=draft.matter_id,
            draft_id=draft.id,
            version_no=draft.current_version_no,
            author_id=p.user_id,
            body=body.body.strip(),
            quote=(body.quote or "").strip()[:2000] or None,
        )
    )
    await db.flush()
    await _audit(
        db, p, "draft.comment", draft, {"version_no": draft.current_version_no, "chars": len(body.body)}
    )
    _changed(db, draft, p, "comments")
    return await detail(db, p, draft_id)


async def resolve_comment(
    db: AsyncSession, p: Principal, draft_id: uuid.UUID, comment_id: uuid.UUID
) -> DraftDetail:
    draft, role = await _draft(db, p, draft_id)
    comment = await db.get(DraftComment, comment_id)
    if comment is None or comment.draft_id != draft.id:
        raise NotFound("Comment not found")
    if comment.author_id != p.user_id and role not in EDIT_ROLES:
        raise Forbidden("Only the author or an editor can resolve this comment")
    if comment.resolved_at is None:
        comment.resolved_by, comment.resolved_at = p.user_id, datetime.now(UTC)
        await db.flush()
        await _audit(db, p, "draft.comment_resolved", draft, {"comment_id": str(comment.id)})
        _changed(db, draft, p, "comments")
    return await detail(db, p, draft_id)


async def heartbeat(db: AsyncSession, p: Principal, draft_id: uuid.UUID, editing: bool) -> Head:
    """Record that I have the draft open; return the latest version number and who else is here."""
    draft, role = await _draft(db, p, draft_id)
    stmt = pg_insert(DraftPresence).values(
        firm_id=p.firm_id,
        matter_id=draft.matter_id,
        draft_id=draft.id,
        user_id=p.user_id,
        editing=editing and role in EDIT_ROLES,
        seen_at=func.now(),
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[DraftPresence.draft_id, DraftPresence.user_id],
            set_={"editing": stmt.excluded.editing, "seen_at": stmt.excluded.seen_at},
        )
    )
    present = [x for x in await _present(db, draft.id) if x.user_id != p.user_id]
    names = await _Names.load(db, [draft.updated_by, *(x.user_id for x in present)])
    return Head(
        current_version_no=draft.current_version_no,
        status=cast(Status, draft.status),
        updated_by=names(draft.updated_by),
        updated_at=draft.updated_at,
        present=[PresenceOut(user=names(x.user_id), editing=x.editing, seen_at=x.seen_at) for x in present],
    )


# --------------------------------------------------------------------------- export


async def export_docx(
    db: AsyncSession, p: Principal, draft_id: uuid.UUID, version_no: int | None
) -> tuple[bytes, str]:
    draft, _ = await _draft(db, p, draft_id)
    n = version_no or draft.current_version_no
    data, filename, _ = await version_docx(db, draft, n, p.user_id)
    await _audit(db, p, "draft.export", draft, {"version_no": n, "format": "docx"})
    return data, filename


async def version_docx(
    db: AsyncSession,
    draft: Draft,
    n: int,
    exporter_id: uuid.UUID,
    signatures: list[tuple[str, str, str]] | None = None,
) -> tuple[bytes, str, str]:
    """Word file of version n, stamped approved or not, plus its file name and plain text. With
    `signatures`, a signature block with invisible anchors is added for the e-signature provider."""
    v = await _version(db, draft, n)
    names = await _Names.load(db, {draft.approved_by, exporter_id})
    if draft.approved_version_no == n and draft.approved_by and draft.approved_at:
        status = f"Version {n} - approved by {names(draft.approved_by).name} on {draft.approved_at:%d %b %Y}"
    elif draft.approved_version_no:
        status = f"Version {n} - NOT APPROVED (version {draft.approved_version_no} is the approved version)"
    else:
        status = f"Version {n} - NOT APPROVED"
    blocks = [[(line.strip(), "")] for line in v.content.splitlines() if line.strip()]
    sources = []
    for s in draft.sources:
        pages = s["page_from"] if s["page_from"] == s["page_to"] else f"{s['page_from']}\u2013{s['page_to']}"
        section = f", {s['section_path']}" if s.get("section_path") else ""
        sources.append(f"[{s['n']}] {s['document_title']}, p. {pages}{section}")
    note = f"Exported from Dataviv AI OS by {names(exporter_id).name} on {datetime.now(UTC):%d %b %Y}. Every version is kept in the matter."
    data = build_docx(draft.title, status, blocks, sources, note, signatures)
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in draft.title)[:60].strip() or "draft"
    return data, f"{safe} v{n}.docx", v.content
