"""Health, identity, dev-token and job-progress endpoints."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DB, CurrentPrincipal
from app.core.config import get_settings
from app.core.errors import NotFound, Unauthorised
from app.core.models import Job
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
        id=job.id, kind=job.kind, status=job.status, progress=job.progress, done=job.done,
        total=job.total, current_step=job.current_step, error=job.error,
    )
