"""Live co-editing of drafts (DRF-08, ADR-020): several people type into one draft at the same time.

Each open draft is a *room* holding a Yjs document (CRDT, via pycrdt) with one text, ``content``. Browsers
run Yjs and speak the standard y-websocket protocol over ``/v1/drafts/{id}/live``: sync (step 1, step 2,
update) and awareness (who is here, cursors). Concurrent keystrokes merge without conflicts or locks.

What stays exactly as in Step 2b (ADR-014): numbered immutable versions, approval of an exact version,
comments, audit. The live document is the *working copy*; "Save version" (or asking for approval) turns
it into the next version, crediting everyone who typed into it since the last one.

Rules the server enforces, whatever the browser does:
- The token travels in the WebSocket sub-protocol (``["dataviv", <token>]``), never in the URL.
- Matter access is checked on connect and every ``RECHECK_SECONDS``: removed or walled → disconnected.
- Viewers, and everyone while the draft waits for approval, are read-only: a change from them is not
  applied and the connection is closed (4403 viewer, 4423 locked) so the browser discards it.
- Every applied change is attributed to the authenticated user (``contributors``); names shown next to
  cursors come from the browser and are display only.

Text offsets: Python counts code points, pycrdt counts UTF-8 bytes, browsers count UTF-16 units. All
server-side edits go through ``Room.replace_span``, which converts, so Hindi and emoji stay intact.
One API process holds the rooms (dev and single-node). Several processes need sticky routing per draft,
or a shared broker; see ADR-020.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, Unauthorised
from app.core.permissions import EDIT_ROLES, matter_role
from app.core.security import Principal, principal_from_token
from app.db.session import after_commit, tenant_session
from app.modules.drafts.models import Draft, DraftLive, DraftVersion

try:  # compiled (Rust) extension: optional, so the rest of the API runs where it is blocked (ADR-011)
    from pycrdt import (
        Awareness,
        Doc,
        Text,
        YSyncMessageType,
        create_awareness_message,
        create_sync_message,
        create_update_message,
        handle_sync_message,
        read_message,
        write_var_uint,
    )

    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only where pycrdt cannot load
    AVAILABLE = False

log = logging.getLogger("dataviv.drafts.live")

TEXT = "content"
MSG_SYNC, MSG_AWARENESS, MSG_QUERY_AWARENESS = 0, 1, 3
MSG_APP = 100  # Dataviv events (JSON) on the same socket: changed, locked, access
CLOSE_UNAUTHORISED, CLOSE_VIEWER, CLOSE_NOT_FOUND, CLOSE_LOCKED, CLOSE_UNAVAILABLE = (
    4401,
    4403,
    4404,
    4423,
    4503,
)
PERSIST_AFTER_SECONDS = 1.0
UNLOAD_AFTER_SECONDS = 60.0
RECHECK_SECONDS = 15.0


class Conn(Protocol):
    user_id: uuid.UUID
    can_edit: bool

    async def send(self, data: bytes) -> None: ...
    async def close(self, code: int, reason: str) -> None: ...


def app_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, default=str).encode()
    return bytes(write_var_uint(MSG_APP)) + bytes(write_var_uint(len(body))) + body


def _cp_to_byte(s: str, i: int) -> int:
    return len(s[:i].encode("utf-8"))


@dataclass(eq=False)
class Room:
    firm_id: uuid.UUID
    matter_id: uuid.UUID
    draft_id: uuid.UUID
    doc: Any
    locked: bool
    contributors: set[uuid.UUID] = field(default_factory=set)
    conns: dict[Conn, set[int]] = field(default_factory=dict)  # connection -> its awareness client ids
    _persist_task: asyncio.Task[None] | None = None
    _unload_task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.text: Any = self.doc.get(TEXT, type=Text)
        self.awareness: Any = Awareness(self.doc)

    # ------------------------------------------------------------------ reading and server-side edits

    def value(self) -> str:
        return str(self.text)

    async def _broadcast(self, data: bytes, exclude: Conn | None = None) -> None:
        targets = [c for c in self.conns if c is not exclude]
        results = await asyncio.gather(*(c.send(data) for c in targets), return_exceptions=True)
        for c, r in zip(targets, results, strict=True):
            if isinstance(r, Exception):
                log.info("dropping a live connection that failed to receive: %s", r)
                self.conns.pop(c, None)

    async def replace_span(self, start: int, length: int, new: str, by: set[uuid.UUID]) -> None:
        """Replace ``length`` characters at ``start`` (code points) and send the change to everyone."""
        s = self.value()
        b0, b1 = _cp_to_byte(s, start), _cp_to_byte(s, start + length)
        before = self.doc.get_state()
        with self.doc.transaction():
            if b1 > b0:
                del self.text[b0:b1]
            if new:
                self.text.insert(b0, new)
        update = self.doc.get_update(before)
        self.contributors |= by
        self.schedule_persist()
        await self._broadcast(create_update_message(update))

    async def replace_all(self, new: str, by: set[uuid.UUID]) -> None:
        """Make the text equal ``new`` with the smallest edit (common prefix and suffix kept)."""
        old = self.value()
        if old == new:
            return
        p = 0
        limit = min(len(old), len(new))
        while p < limit and old[p] == new[p]:
            p += 1
        q = 0
        while q < limit - p and old[len(old) - 1 - q] == new[len(new) - 1 - q]:
            q += 1
        await self.replace_span(p, len(old) - p - q, new[p : len(new) - q], by)

    async def event(self, payload: dict[str, Any]) -> None:
        await self._broadcast(app_message(payload))

    async def set_locked(self, locked: bool, status: str) -> None:
        self.locked = locked
        await self.event({"type": "status", "status": status, "locked": locked})

    # ------------------------------------------------------------------ connections

    async def join(self, conn: Conn) -> None:
        if self._unload_task is not None:
            self._unload_task.cancel()
            self._unload_task = None
        self.conns[conn] = set()
        await conn.send(app_message({"type": "hello", "can_edit": conn.can_edit, "locked": self.locked}))
        await conn.send(create_sync_message(self.doc))  # ask the browser for anything it has that we lack
        others = [cid for cid in self.awareness.states if cid != self.doc.client_id]
        if others:
            await conn.send(create_awareness_message(self.awareness.encode_awareness_update(others)))

    async def leave(self, conn: Conn) -> None:
        ids = self.conns.pop(conn, set())
        if ids:
            self.awareness.remove_awareness_states(list(ids), conn)
            await self._broadcast(create_awareness_message(self.awareness.encode_awareness_update(list(ids))))
        if not self.conns:
            self._unload_task = asyncio.create_task(self._unload_later())

    def _changes_text(self, update: bytes) -> bool:
        """Would applying ``update`` change anything? Checked on a scratch copy, never on the room."""
        scratch: Any = Doc()
        scratch.apply_update(self.doc.get_update())
        scratch_text = scratch.get(TEXT, type=Text)
        before_text, before_state = str(scratch_text), scratch.get_state()
        scratch.apply_update(update)
        return str(scratch_text) != before_text or scratch.get_state() != before_state

    async def handle(self, conn: Conn, msg: bytes) -> None:
        if not msg:
            return
        kind = msg[0]
        if kind == MSG_SYNC and len(msg) > 1:
            sub = msg[1]
            if sub == YSyncMessageType.SYNC_STEP1:
                reply = handle_sync_message(msg[1:], self.doc)
                if reply:
                    await conn.send(reply)
                return
            if sub not in (YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE):
                return
            update = read_message(msg[2:])
            if not conn.can_edit or self.locked:
                if self._changes_text(update):
                    code = CLOSE_VIEWER if not conn.can_edit else CLOSE_LOCKED
                    reason = "Read only: viewers cannot edit" if not conn.can_edit else "Waiting for approval"
                    self.conns.pop(conn, None)
                    await conn.close(code, reason)
                return
            before, before_state = self.value(), self.doc.get_state()
            handle_sync_message(msg[1:], self.doc)
            text_changed = self.value() != before
            if text_changed:
                self.contributors.add(conn.user_id)
            if text_changed or self.doc.get_state() != before_state:
                self.schedule_persist()
                await self._broadcast(create_update_message(update), exclude=conn)
        elif kind == MSG_AWARENESS:
            payload = read_message(msg[1:])
            known = set(self.awareness.states)
            self.awareness.apply_awareness_update(payload, conn)
            if conn in self.conns:
                self.conns[conn] |= set(self.awareness.states) - known
            await self._broadcast(msg, exclude=conn)
        elif kind == MSG_QUERY_AWARENESS:
            ids = [cid for cid in self.awareness.states if cid != self.doc.client_id]
            if ids:
                await conn.send(create_awareness_message(self.awareness.encode_awareness_update(ids)))

    # ------------------------------------------------------------------ persistence

    def schedule_persist(self) -> None:
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = asyncio.create_task(self._persist_later())

    async def _persist_later(self) -> None:
        await asyncio.sleep(PERSIST_AFTER_SECONDS)
        await self.persist()

    async def persist(self, db: AsyncSession | None = None) -> None:
        values = {
            "draft_id": self.draft_id,
            "firm_id": self.firm_id,
            "matter_id": self.matter_id,
            "state": self.doc.get_update(),
            "contributors": sorted(self.contributors),
        }
        stmt = pg_insert(DraftLive).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[DraftLive.draft_id],
            set_={
                "state": stmt.excluded.state,
                "contributors": stmt.excluded.contributors,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        try:
            if db is not None:
                await db.execute(stmt)
            else:
                async with tenant_session(self.firm_id) as s:
                    await s.execute(stmt)
        except Exception:
            log.exception("could not save the live copy of draft %s", self.draft_id)

    async def _unload_later(self) -> None:
        await asyncio.sleep(UNLOAD_AFTER_SECONDS)
        if not self.conns:
            await self.persist()
            _rooms.pop(self.draft_id, None)


# ---------------------------------------------------------------------- registry

_rooms: dict[uuid.UUID, Room] = {}
_loading: dict[uuid.UUID, asyncio.Lock] = {}


async def room_for(db: AsyncSession, draft: Draft) -> Room | None:
    """The live room of a draft, loaded (or created from the current version) on first use.

    Uses the caller's session, so it is safe inside a transaction that holds the draft's row lock.
    Returns None where live editing is unavailable (pycrdt cannot load).
    """
    if not AVAILABLE:
        return None
    room = _rooms.get(draft.id)
    if room is not None:
        return room
    lock = _loading.setdefault(draft.id, asyncio.Lock())
    async with lock:
        room = _rooms.get(draft.id)
        if room is not None:
            return room
        saved = await db.get(DraftLive, draft.id)
        doc: Any = Doc()
        if saved is not None:
            doc.apply_update(saved.state)
            contributors = set(saved.contributors or [])
        else:
            content = await db.scalar(
                select(DraftVersion.content).where(
                    DraftVersion.draft_id == draft.id, DraftVersion.version_no == draft.current_version_no
                )
            )
            doc.get(TEXT, type=Text).insert(0, content or "")
            contributors = set()
        room = Room(draft.firm_id, draft.matter_id, draft.id, doc, draft.status == "in_review", contributors)
        if saved is None:
            await room.persist(db)  # the base every browser builds on must exist before anyone edits
        _rooms[draft.id] = room
        return room


def loaded(draft_id: uuid.UUID) -> Room | None:
    return _rooms.get(draft_id)


def notify(db: AsyncSession, draft_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Tell everyone in the draft's room once this transaction commits, so a refetch sees the change."""

    async def send() -> None:
        room = _rooms.get(draft_id)
        if room is not None:
            await room.event(payload)

    after_commit(db, send)


async def flush_all() -> None:
    """Tests: wait for pending saves of every room."""
    tasks = [r._persist_task for r in _rooms.values() if r._persist_task and not r._persist_task.done()]
    for t in tasks:
        with contextlib.suppress(Exception):
            await t


async def reset() -> None:
    """Tests: forget every room (they are reloaded from the database on next use)."""
    await flush_all()
    for r in _rooms.values():
        for t in (r._persist_task, r._unload_task):
            if t is not None and not t.done():
                t.cancel()
    _rooms.clear()
    _loading.clear()


# ---------------------------------------------------------------------- the socket


@dataclass(eq=False)
class WsConn:
    ws: WebSocket
    user_id: uuid.UUID
    can_edit: bool

    async def send(self, data: bytes) -> None:
        await self.ws.send_bytes(data)

    async def close(self, code: int, reason: str) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code=code, reason=reason)


async def _close(ws: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code, reason=reason)


async def _recheck(conn: WsConn, p: Principal, room: Room) -> None:
    """Re-check matter access while connected: removed or walled people are disconnected."""
    while True:
        await asyncio.sleep(RECHECK_SECONDS)
        async with tenant_session(p.firm_id) as db:
            role = await matter_role(db, p, room.matter_id)
        if role is None:
            room.conns.pop(conn, None)
            await conn.close(CLOSE_NOT_FOUND, "You no longer have access to this matter")
            return
        can_edit = role in EDIT_ROLES
        if can_edit != conn.can_edit:
            conn.can_edit = can_edit
            await conn.send(app_message({"type": "access", "can_edit": can_edit}))


async def serve(ws: WebSocket, draft_id: uuid.UUID) -> None:
    """One browser in one draft: authenticate, check access, then relay Yjs messages until it leaves."""
    from app.modules.drafts.service import draft_for_live

    offered = [x.strip() for x in ws.headers.get("sec-websocket-protocol", "").split(",") if x.strip()]
    ours = bool(offered) and offered[0] == "dataviv"
    await ws.accept(subprotocol="dataviv" if ours else None)  # accept first, so close codes reach the browser
    if not AVAILABLE:
        return await _close(ws, CLOSE_UNAVAILABLE, "Live editing is not available on this server")
    if not ours or len(offered) < 2:
        return await _close(ws, CLOSE_UNAUTHORISED, "Missing token")
    try:
        p = principal_from_token(offered[1])
    except Unauthorised:
        return await _close(ws, CLOSE_UNAUTHORISED, "Invalid or expired token")
    try:
        async with tenant_session(p.firm_id) as db:
            draft, role = await draft_for_live(db, p, draft_id)
            room = await room_for(db, draft)
    except NotFound:
        return await _close(ws, CLOSE_NOT_FOUND, "Draft not found")
    assert room is not None
    conn = WsConn(ws, p.user_id, role in EDIT_ROLES)
    await room.join(conn)
    recheck = asyncio.create_task(_recheck(conn, p, room))
    try:
        while conn in room.conns:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                await room.handle(conn, data)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        recheck.cancel()
        await room.leave(conn)
