"""Append-only, hash-chained audit trail (FND-05).

Each firm has its own chain: hash = sha256(prev_hash + canonical JSON of the event).
A per-firm advisory lock serialises appends so the chain never forks.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import AuditEvent
from app.core.security import Principal

GENESIS = "0" * 64


def _digest(prev_hash: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def _payload(e: AuditEvent) -> dict[str, Any]:
    return {
        "firm_id": str(e.firm_id),
        "actor_id": str(e.actor_id) if e.actor_id else None,
        "action": e.action,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "matter_id": str(e.matter_id) if e.matter_id else None,
        "detail": e.detail,
    }


async def record(
    db: AsyncSession,
    principal: Principal,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID | None = None,
    matter_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{principal.firm_id}"})
    prev = await db.scalar(
        select(AuditEvent.hash)
        .where(AuditEvent.firm_id == principal.firm_id)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    event = AuditEvent(
        firm_id=principal.firm_id,
        actor_id=principal.user_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id else None,
        matter_id=matter_id,
        # normalise to JSON-safe values so the stored JSONB hashes identically when re-read
        detail=json.loads(json.dumps(detail or {}, default=str)),
        prev_hash=prev or GENESIS,
        hash="",
    )
    event.hash = _digest(event.prev_hash, _payload(event))
    db.add(event)
    await db.flush()
    return event


async def record_isolated(
    principal: Principal,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID | None = None,
    matter_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record in its own transaction, so the event survives when the request's transaction rolls
    back (used for access denials, which raise right after recording)."""
    from app.db.session import tenant_session  # local import avoids a cycle at module load

    async with tenant_session(principal.firm_id) as s:
        await record(s, principal, action, object_type, object_id, matter_id, detail)


async def verify_chain(db: AsyncSession, firm_id: uuid.UUID) -> bool:
    """Recompute the firm's chain; False if any event was altered, removed or reordered."""
    events = (
        await db.scalars(select(AuditEvent).where(AuditEvent.firm_id == firm_id).order_by(AuditEvent.id))
    ).all()
    prev = GENESIS
    for e in events:
        if e.prev_hash != prev or e.hash != _digest(prev, _payload(e)):
            return False
        prev = e.hash
    return True
