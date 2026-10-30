"""Health, identity, users, dev identities and job-progress endpoints."""

import uuid
from functools import lru_cache

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.api.deps import DB, CurrentPrincipal
from app.core.config import get_settings
from app.core.errors import NotFound, Unauthorised
from app.core.models import Job, User
from app.core.security import Role, issue_dev_token
from app.db.session import get_engine

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "env": get_settings().env}


@router.get("/v1/me")
async def me(p: CurrentPrincipal) -> dict[str, str]:
    return {"user_id": str(p.user_id), "firm_id": str(p.firm_id), "role": p.role, "email": p.email}


class DevTokenRequest(BaseModel):
    user_id: uuid.UUID
    firm_id: uuid.UUID
    role: Role
    email: str


@router.post("/v1/dev/token", include_in_schema=False)
async def dev_token(body: DevTokenRequest) -> dict[str, str]:
    """Dev/test only: mint a token for a seeded user. Disabled in staging and production."""
    if not get_settings().dev_auth_enabled:
        raise Unauthorised("Not available")
    return {"access_token": issue_dev_token(body.user_id, body.firm_id, body.role, body.email)}


class UserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str


@router.get("/v1/users", response_model=list[UserOut])
async def list_users(p: CurrentPrincipal, db: DB) -> list[UserOut]:
    """Active users of the caller's firm (RLS limits this to one firm), e.g. to pick matter members."""
    rows = await db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name))
    return [UserOut(id=u.id, name=u.name, email=u.email, role=u.role) for u in rows]


class DevIdentity(BaseModel):
    firm_id: uuid.UUID
    firm_name: str
    user_id: uuid.UUID
    name: str
    email: str
    role: str
    access_token: str


@lru_cache
def _owner_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_owner_url, pool_size=1)


@router.get("/v1/dev/identities", response_model=list[DevIdentity], include_in_schema=False)
async def dev_identities() -> list[DevIdentity]:
    """Dev/test only: every user of every firm with a fresh token, so the test console can switch roles.
    Reads across firms with the owner connection, which is why it never exists outside dev/test."""
    if not get_settings().dev_auth_enabled:
        raise NotFound("Not found")
    async with _owner_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT f.id, f.name, u.id, u.name, u.email, u.role FROM users u "
                    "JOIN firms f ON f.id = u.firm_id WHERE u.is_active ORDER BY f.name, u.role, u.name"
                )
            )
        ).all()
    return [
        DevIdentity(
            firm_id=r[0],
            firm_name=r[1],
            user_id=r[2],
            name=r[3],
            email=r[4],
            role=r[5],
            access_token=issue_dev_token(r[2], r[0], r[5], r[4]),
        )
        for r in rows
    ]


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    progress: int
    done: int
    total: int
    current_step: str | None
    error: str | None


@router.get("/v1/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> JobOut:
    job = await db.get(Job, job_id)
    if job is None or (job.created_by != p.user_id and not p.is_admin):
        raise NotFound("Job not found")
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        done=job.done,
        total=job.total,
        current_step=job.current_step,
        error=job.error,
    )
