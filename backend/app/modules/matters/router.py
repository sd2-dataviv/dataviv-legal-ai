import uuid

from fastapi import APIRouter, status

from app.api.deps import DB, CurrentPrincipal
from app.modules.matters import service
from app.modules.matters.schemas import (
    MatterCreate,
    MatterOut,
    MatterPeople,
    MemberAdd,
    MemberOut,
    WallCreate,
)

router = APIRouter(prefix="/v1/matters", tags=["matters"])


def _out(matter: object, role: str | None) -> MatterOut:
    out = MatterOut.model_validate(matter)
    out.my_role = role  # type: ignore[assignment]
    return out


@router.post("", response_model=MatterOut, status_code=status.HTTP_201_CREATED)
async def create_matter(body: MatterCreate, p: CurrentPrincipal, db: DB) -> MatterOut:
    matter, role = await service.create_matter(db, p, body)
    return _out(matter, role)


@router.get("", response_model=list[MatterOut])
async def list_matters(p: CurrentPrincipal, db: DB) -> list[MatterOut]:
    return [_out(m, r) for m, r in await service.list_matters(db, p)]


@router.get("/{matter_id}", response_model=MatterOut)
async def get_matter(matter_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> MatterOut:
    matter, role = await service.get_matter(db, p, matter_id)
    return _out(matter, role)


@router.get("/{matter_id}/people", response_model=MatterPeople)
async def matter_people(matter_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> MatterPeople:
    """Members (with names and roles) and ethical walls on this matter."""
    return await service.matter_people(db, p, matter_id)


@router.post("/{matter_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(matter_id: uuid.UUID, body: MemberAdd, p: CurrentPrincipal, db: DB) -> MemberOut:
    return MemberOut.model_validate(await service.add_member(db, p, matter_id, body))


@router.post("/{matter_id}/walls", status_code=status.HTTP_201_CREATED)
async def create_wall(matter_id: uuid.UUID, body: WallCreate, p: CurrentPrincipal, db: DB) -> dict[str, str]:
    wall = await service.create_wall(db, p, matter_id, body)
    return {"id": str(wall.id)}
