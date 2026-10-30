"""The single source of truth for matter access (FND-03, FND-04; ADR-004).

API routes, retrieval filters and agent tools ALL call these functions. Never write an ad-hoc
membership query elsewhere: when OpenFGA replaces the internals, only this file changes.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Forbidden, NotFound
from app.core.security import Principal
from app.modules.matters.models import EthicalWall, MatterMember

# Matter roles that may manage membership and approve gated actions
MANAGER_ROLES = frozenset({"lead", "partner"})
# Matter roles that may change matter work product (drafts, review tables); viewers only read and comment
EDIT_ROLES = frozenset({"lead", "partner", "member"})


async def accessible_matter_ids(db: AsyncSession, principal: Principal) -> set[uuid.UUID]:
    """Matters the principal is a member of, minus any they are walled from.

    Admins get NO implicit content access (SRS §2): they must be members like anyone else.
    """
    member_of = select(MatterMember.matter_id).where(MatterMember.user_id == principal.user_id)
    walled = select(EthicalWall.matter_id).where(EthicalWall.user_id == principal.user_id)
    rows = await db.scalars(member_of.except_(walled))
    return set(rows.all())


async def matter_role(db: AsyncSession, principal: Principal, matter_id: uuid.UUID) -> str | None:
    """The principal's role on a matter, or None if not a member or walled."""
    return await user_matter_role(db, principal.user_id, matter_id)


async def user_matter_role(db: AsyncSession, user_id: uuid.UUID, matter_id: uuid.UUID) -> str | None:
    """Any user's role on a matter (e.g. a chosen approver), or None if not a member or walled."""
    walled = await db.scalar(
        select(EthicalWall.id).where(EthicalWall.user_id == user_id, EthicalWall.matter_id == matter_id)
    )
    if walled:
        return None
    return await db.scalar(
        select(MatterMember.matter_role).where(
            MatterMember.user_id == user_id, MatterMember.matter_id == matter_id
        )
    )


async def require_matter_access(db: AsyncSession, principal: Principal, matter_id: uuid.UUID) -> str:
    """Return the principal's matter role, or raise NotFound (ADR-007) and audit the denial."""
    role = await matter_role(db, principal, matter_id)
    if role is None:
        await audit.record_isolated(principal, "matter.access_denied", "matter", matter_id, matter_id)
        raise NotFound("Matter not found")
    return role


async def require_matter_manager(db: AsyncSession, principal: Principal, matter_id: uuid.UUID) -> None:
    role = await require_matter_access(db, principal, matter_id)
    if role not in MANAGER_ROLES:
        raise Forbidden("Only the matter lead or a partner on the matter can do this")
