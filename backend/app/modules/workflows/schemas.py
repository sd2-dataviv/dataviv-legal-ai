import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Person(BaseModel):
    id: uuid.UUID
    name: str


class Clause(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    title: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=5, max_length=500)
    preferred: str = Field(min_length=1, max_length=1000)
    fallback: str = Field(default="", max_length=1000)
    unacceptable: str = Field(default="", max_length=1000)
    guidance: str = Field(default="", max_length=1000)


class PlaybookIn(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    contract_type: str = Field(default="general", max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    clauses: list[Clause] = Field(min_length=1, max_length=60)


class PlaybookCreate(BaseModel):
    from_template: str | None = Field(default=None, description="Copy a sample playbook, e.g. spa-buyer")
    playbook: PlaybookIn | None = None


class PlaybookOut(PlaybookIn):
    id: uuid.UUID
    version: int
    updated_by: Person
    updated_at: datetime


class WorkflowSummary(BaseModel):
    slug: str
    version: int
    name: str
    description: str
    builtin: bool
    inputs: list[dict[str, Any]]
    steps: list[dict[str, str]]


class SpecIn(BaseModel):
    spec: dict[str, Any]


class SpecCheck(BaseModel):
    valid: bool
    problems: list[str]


class RunCreate(BaseModel):
    workflow: str = Field(description="The workflow's slug")
    version: int | None = Field(default=None, description="Default: the latest version")
    inputs: dict[str, Any] = {}


class StepOut(BaseModel):
    id: uuid.UUID
    position: int
    key: str
    type: str
    title: str
    status: Literal["pending", "running", "waiting", "done", "failed", "skipped"]
    resolved: dict[str, Any]
    output: dict[str, Any]
    model: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    matter_id: uuid.UUID
    step_id: uuid.UUID
    required_role: str
    message: str | None
    status: Literal["pending", "approved", "rejected", "cancelled"]
    requested_by: Person
    decided_by: Person | None
    decided_at: datetime | None
    comment: str | None
    created_at: datetime
    can_decide: bool = False
    run_name: str | None = None
    subject: Literal["workflow", "agent"] = "workflow"  # run_id / step_id refer to this kind of run
    detail: dict[str, Any] = {}  # agent actions: tool, arguments, thought


class JobOut(BaseModel):
    id: uuid.UUID
    status: str
    done: int
    total: int
    progress: int
    current_step: str | None


class RunSummary(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    slug: str
    version: int
    name: str
    status: Literal["queued", "running", "waiting_approval", "succeeded", "failed", "rejected", "cancelled"]
    created_by: Person
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    cost_usd: float
    error: str | None
    steps_done: int
    steps_total: int


class RunDetail(RunSummary):
    inputs: dict[str, Any]
    input_tokens: int
    output_tokens: int
    steps: list[StepOut]
    approvals: list[ApprovalOut]
    job: JobOut | None
    can_cancel: bool
    can_retry: bool


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=4000)
