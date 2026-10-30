import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.workflows.schemas import ApprovalOut, JobOut, Person

RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "waiting_subagents",
    "succeeded",
    "stopped",
    "failed",
    "cancelled",
]
MemoryKind = Literal["fact", "event", "party", "deadline", "note"]


class AgentSummary(BaseModel):
    slug: str
    version: int
    name: str
    description: str
    builtin: bool
    goal_hint: str
    tools: list[dict[str, str]]  # name, policy
    max_steps: int
    max_cost_usd: float


class SpecIn(BaseModel):
    spec: dict[str, Any]


class SpecCheck(BaseModel):
    valid: bool
    problems: list[str]


class RunCreate(BaseModel):
    agent: str = Field(description="The agent's slug")
    goal: str = Field(min_length=5, max_length=4000)
    document_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=50, description="Default: whole matter"
    )
    max_steps: int | None = Field(default=None, ge=1, le=30, description="At most the agent's own limit")
    max_cost_usd: float | None = Field(default=None, gt=0, le=1, description="At most the agent's own limit")


class StepOut(BaseModel):
    id: uuid.UUID
    position: int
    thought: str | None
    tool: str
    arguments: dict[str, Any]
    status: Literal["running", "waiting_approval", "approved", "waiting", "done", "rejected", "failed"]
    observation: str | None
    result: dict[str, Any]
    model: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class ChildOut(BaseModel):
    id: uuid.UUID
    name: str
    goal: str
    status: RunStatus
    steps_used: int
    cost_usd: float
    answer: str | None


class RunSummary(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    slug: str
    version: int
    name: str
    goal: str
    status: RunStatus
    created_by: Person
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    steps_used: int
    max_steps: int
    cost_usd: float
    max_cost_usd: float
    error: str | None


class RunDetail(RunSummary):
    parent_run_id: uuid.UUID | None
    scope_document_ids: list[uuid.UUID] | None
    tools: list[dict[str, str]]
    input_tokens: int
    output_tokens: int
    answer: str | None
    sentences: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    verification: dict[str, Any]
    steps: list[StepOut]
    approvals: list[ApprovalOut]
    children: list[ChildOut]
    job: JobOut | None
    can_cancel: bool


class MemoryIn(BaseModel):
    kind: MemoryKind = "note"
    subject: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=2, max_length=2000)
    event_date: date | None = None
    document_id: uuid.UUID | None = None


class MemoryOut(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    kind: MemoryKind
    subject: str
    content: str
    event_date: date | None
    document_id: uuid.UUID | None
    document_title: str | None
    chunk_id: uuid.UUID | None
    page_from: int | None
    page_to: int | None
    source: Literal["user", "agent"]
    agent_run_id: uuid.UUID | None
    created_by: Person
    created_at: datetime
