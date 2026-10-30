import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, CurrentPrincipal

from . import service
from .schemas import (
    AgentSummary,
    MemoryIn,
    MemoryKind,
    MemoryOut,
    RunCreate,
    RunDetail,
    RunSummary,
    SpecCheck,
    SpecIn,
)

router = APIRouter(tags=["agents"])


@router.get("/v1/agents", response_model=list[AgentSummary])
async def list_agents(p: CurrentPrincipal, db: DB) -> list[AgentSummary]:
    """Built-in agents plus the firm's own (latest version of each), with their tools and budgets."""
    return await service.list_agents(db, p)


@router.get("/v1/agents/{slug}")
async def get_agent(slug: str, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    """The full definition: instructions, tools, approval policy, budget."""
    return await service.get_agent(db, p, slug)


@router.post("/v1/agents/validate", response_model=SpecCheck)
async def validate_agent(body: SpecIn, p: CurrentPrincipal, db: DB) -> SpecCheck:
    return await service.check_spec(db, body.spec)


@router.post("/v1/agents", response_model=AgentSummary, status_code=status.HTTP_201_CREATED)
async def save_agent(body: SpecIn, p: CurrentPrincipal, db: DB) -> AgentSummary:
    """Publish a firm agent (partners and admins). Re-publishing a slug adds a version."""
    return await service.save_agent(db, p, body.spec)


@router.post(
    "/v1/matters/{matter_id}/agent-runs", response_model=RunDetail, status_code=status.HTTP_201_CREATED
)
async def start_run(matter_id: uuid.UUID, body: RunCreate, p: CurrentPrincipal, db: DB) -> RunDetail:
    """Start an agent on this matter. It works in the background, as you, inside this matter only."""
    return await service.start_run(db, p, matter_id, body)


@router.get("/v1/matters/{matter_id}/agent-runs", response_model=list[RunSummary])
async def list_runs(matter_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> list[RunSummary]:
    return await service.list_runs(db, p, matter_id)


@router.get("/v1/agent-runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> RunDetail:
    """The full trace: every thought, tool call, observation, approval, subagent, token and cost."""
    return await service.run_detail(db, p, run_id)


@router.post("/v1/agent-runs/{run_id}/cancel", response_model=RunDetail)
async def cancel_run(run_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> RunDetail:
    """Stops the agent and its subagents; pending approvals are cancelled."""
    return await service.cancel(db, p, run_id)


@router.get("/v1/matters/{matter_id}/memory", response_model=list[MemoryOut])
async def list_memory(
    matter_id: uuid.UUID,
    p: CurrentPrincipal,
    db: DB,
    q: Annotated[str | None, Query(max_length=200)] = None,
    kind: MemoryKind | None = None,
) -> list[MemoryOut]:
    """Facts, events, parties, deadlines and notes saved for this matter, by people and agents."""
    return await service.list_memory(db, p, matter_id, q, kind)


@router.post("/v1/matters/{matter_id}/memory", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def add_memory(matter_id: uuid.UUID, body: MemoryIn, p: CurrentPrincipal, db: DB) -> MemoryOut:
    return await service.add_memory(db, p, matter_id, body)


@router.delete("/v1/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    await service.delete_memory(db, p, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
