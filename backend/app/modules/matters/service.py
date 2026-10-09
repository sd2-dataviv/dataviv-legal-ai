"""Matter business logic. No HTTP here; routers call these functions."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.models import User
from app.core.permissions import accessible_matter_ids, require_matter_access, require_matter_manager
from app.core.security import Principal
from app.modules.matters.models import EthicalWall, Matter, MatterMember
from app.modules.matters.schemas import MatterCreate, MemberAdd, WallCreate

CAN_OPEN_MATTERS = frozenset({"partner", "associate", "admin"})


async def create_matter(db: AsyncSession, p: Principal, data: MatterCreate) -> tuple[Matter, str]:
    if p.role not in CAN_OPEN_MATTERS:
        raise Forbidden("Your role cannot open matters")
    matter = Matter(firm_id=p.firm_id, created_by=p.user_id, **data.model_dump())
    db.add(matter)
    await db.flush()
    db.add(MatterMember(firm_id=p.firm_id, matter_id=matter.id, user_id=p.user_id, matter_role="lead"))
    await audit.record(db, p, "matter.create", "matter", matter.id, matter.id, {"title": matter.title})
    return matter, "lead"


async def list_matters(db: AsyncSession, p: Principal) -> list[tuple[Matter, str]]:
    ids = await accessible_matter_ids(db, p)
    if not ids:
        return []
    rows = await db.execute(
        select(Matter, MatterMember.matter_role)
        .join(MatterMember, (MatterMember.matter_id == Matter.id) & (MatterMember.user_id == p.user_id))
        .where(Matter.id.in_(ids))
        .order_by(Matter.created_at.desc())
    )
    return [(m, r) for m, r in rows.all()]


async def get_matter(db: AsyncSession, p: Principal, matter_id: uuid.UUID) -> tuple[Matter, str]:
    role = await require_matter_access(db, p, matter_id)
    matter = await db.get(Matter, matter_id)
    if matter is None:  # pragma: no cover - membership implies existence
        raise NotFound("Matter not found")
    return matter, role


async def add_member(db: AsyncSession, p: Principal, matter_id: uuid.UUID, data: MemberAdd) -> MatterMember:
    await require_matter_manager(db, p, matter_id)
    if await db.get(User, data.user_id) is None:  # RLS: users of other firms are invisible
        raise NotFound("User not found")
    if await db.get(MatterMember, (matter_id, data.user_id)):
        raise Conflict("User is already a member")
    walled = await db.scalar(
        select(EthicalWall.id).where(EthicalWall.matter_id == matter_id, EthicalWall.user_id == data.user_id)
    )
    if walled:
        raise Conflict("User is behind an ethical wall for this matter")
    member = MatterMember(
        firm_id=p.firm_id, matter_id=matter_id, user_id=data.user_id, matter_role=data.matter_role
    )
    db.add(member)
    await audit.record(db, p, "matter.member_add", "matter", matter_id, matter_id, data.model_dump())
    return member


async def create_wall(db: AsyncSession, p: Principal, matter_id: uuid.UUID, data: WallCreate) -> EthicalWall:
    """Admins (conflicts team) raise walls; a wall also removes any existing membership."""
    if not p.is_admin:
        raise Forbidden("Only firm admins can raise ethical walls")
    if await db.get(Matter, matter_id) is None:
        raise NotFound("Matter not found")
    if await db.get(User, data.user_id) is None:
        raise NotFound("User not found")
    wall = EthicalWall(
        firm_id=p.firm_id, matter_id=matter_id, user_id=data.user_id, reason=data.reason, created_by=p.user_id
    )
    db.add(wall)
    await db.execute(
        delete(MatterMember).where(MatterMember.matter_id == matter_id, MatterMember.user_id == data.user_id)
    )
    await audit.record(db, p, "matter.wall_create", "matter", matter_id, matter_id, data.model_dump())
    return wall
