import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, CurrentPrincipal
from app.modules.agents.schemas import RunDetail as AgentRunDetail
from app.modules.workflows import service
from app.modules.workflows.schemas import (
    ApprovalOut,
    Decision,
    PlaybookCreate,
    PlaybookIn,
    PlaybookOut,
    RunCreate,
    RunDetail,
    RunSummary,
    SpecCheck,
    SpecIn,
    WorkflowSummary,
)

router = APIRouter(tags=["workflows"])


@router.get("/v1/workflows", response_model=list[WorkflowSummary])
async def list_workflows(p: CurrentPrincipal, db: DB) -> list[WorkflowSummary]:
    """Built-in workflows plus the firm's own (latest version of each)."""
    return await service.list_workflows(db, p)


@router.get("/v1/workflows/{slug}")
async def get_workflow(slug: str, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    """The full definition: inputs and steps."""
    return await service.get_workflow(db, p, slug)


@router.post("/v1/workflows/validate", response_model=SpecCheck)
async def validate_workflow(body: SpecIn, p: CurrentPrincipal) -> SpecCheck:
    """Check a definition without saving it; every problem is listed."""
    return service.check_spec(body.spec)


@router.post("/v1/workflows", response_model=WorkflowSummary, status_code=status.HTTP_201_CREATED)
async def save_workflow(body: SpecIn, p: CurrentPrincipal, db: DB) -> WorkflowSummary:
    """Publish a firm workflow (partners and admins). Saving an existing slug adds a new version;
    runs already started keep the version they started with."""
    return await service.save_workflow(db, p, body.spec)


@router.post(
    "/v1/matters/{matter_id}/workflow-runs", response_model=RunDetail, status_code=status.HTTP_201_CREATED
)
async def start_run(matter_id: uuid.UUID, body: RunCreate, p: CurrentPrincipal, db: DB) -> RunDetail:
    """Start a workflow on this matter. Inputs are checked first; the run then proceeds in the background."""
    return await service.start_run(db, p, matter_id, body)


@router.get("/v1/matters/{matter_id}/workflow-runs", response_model=list[RunSummary])
async def list_runs(matter_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> list[RunSummary]:
    return await service.list_runs(db, p, matter_id)


@router.get("/v1/workflow-runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> RunDetail:
    """Every step with its inputs, output, model, tokens and cost, plus approvals and progress."""
    return await service.run_detail(db, p, run_id)


@router.post("/v1/workflow-runs/{run_id}/cancel", response_model=RunDetail)
async def cancel_run(run_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> RunDetail:
    return await service.cancel(db, p, run_id)


@router.post("/v1/workflow-runs/{run_id}/retry", response_model=RunDetail)
async def retry_run(run_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> RunDetail:
    """Carry on from the step that failed; finished steps are not repeated."""
    return await service.retry(db, p, run_id)


@router.get("/v1/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    p: CurrentPrincipal, db: DB, mine: Annotated[bool, Query(description="Only those you can decide")] = False
) -> list[ApprovalOut]:
    return await service.list_approvals(db, p, mine)


@router.post("/v1/approvals/{approval_id}/decision", response_model=RunDetail | AgentRunDetail)
async def decide(
    approval_id: uuid.UUID, body: Decision, p: CurrentPrincipal, db: DB
) -> RunDetail | AgentRunDetail:
    """Workflow steps: approve (the run carries on) or reject (with a comment; the run stops).
    Agent actions: approve (the action runs) or reject (the agent is told why and carries on).
    Returns the run the approval belongs to."""
    result: RunDetail | AgentRunDetail = await service.decide(db, p, approval_id, body)
    return result


@router.get("/v1/playbook-templates")
async def playbook_templates(p: CurrentPrincipal) -> list[dict[str, Any]]:
    """Sample playbooks to copy and adapt."""
    return service.playbook_templates()


@router.get("/v1/playbooks", response_model=list[PlaybookOut])
async def list_playbooks(p: CurrentPrincipal, db: DB) -> list[PlaybookOut]:
    return await service.list_playbooks(db, p)


@router.post("/v1/playbooks", response_model=PlaybookOut, status_code=status.HTTP_201_CREATED)
async def create_playbook(body: PlaybookCreate, p: CurrentPrincipal, db: DB) -> PlaybookOut:
    """Partners and admins. Start from a template (`from_template`) or give the clauses."""
    return await service.create_playbook(db, p, body)


@router.get("/v1/playbooks/{playbook_id}", response_model=PlaybookOut)
async def get_playbook(playbook_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> PlaybookOut:
    return await service.get_playbook(db, p, playbook_id)


@router.put("/v1/playbooks/{playbook_id}", response_model=PlaybookOut)
async def update_playbook(
    playbook_id: uuid.UUID, body: PlaybookIn, p: CurrentPrincipal, db: DB
) -> PlaybookOut:
    """Each save increases the version; runs already started keep the version they read."""
    return await service.update_playbook(db, p, playbook_id, body)


@router.delete("/v1/playbooks/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playbook(playbook_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    await service.delete_playbook(db, p, playbook_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
