import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AnswerType = Literal["text", "yes_no", "date", "amount", "choice"]
CellStatus = Literal["empty", "queued", "done", "not_found", "failed"]


class ColumnIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=5, max_length=500)
    answer_type: AnswerType = "text"
    choices: list[str] = Field(default=[], max_length=20)


class ColumnPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    question: str | None = Field(default=None, min_length=5, max_length=500)
    answer_type: AnswerType | None = None
    choices: list[str] | None = Field(default=None, max_length=20)


class TableCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    template: Literal["contracts", "litigation", "notices"] | None = None
    columns: list[ColumnIn] = Field(default=[], max_length=30)
    document_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=10_000, description="Default: every indexed document in the matter"
    )


class TablePatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class DocumentsIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=10_000)


class RunIn(BaseModel):
    scope: Literal["missing", "all"] = Field(default="missing", description="missing = empty or failed cells")
    column_ids: list[uuid.UUID] | None = None
    document_ids: list[uuid.UUID] | None = None


class CellPatch(BaseModel):
    answer: str | None = Field(default=None, max_length=4000, description="A person's correction")
    revert: bool = Field(default=False, description="Drop the correction and show the model's answer again")
    reviewed: bool | None = None


class Person(BaseModel):
    id: uuid.UUID
    name: str


class ColumnOut(BaseModel):
    id: uuid.UUID
    position: int
    label: str
    question: str
    answer_type: AnswerType
    choices: list[str]


class RowOut(BaseModel):
    document_id: uuid.UUID
    title: str
    doc_type: str
    status: str
    page_count: int | None


class CellOut(BaseModel):
    id: uuid.UUID
    column_id: uuid.UUID
    document_id: uuid.UUID
    status: CellStatus
    answer: str | None
    model_answer: str | None
    value: str | None
    quote: str | None
    chunk_id: uuid.UUID | None
    page_from: int | None
    page_to: int | None
    verified: bool | None
    flag: str | None
    error: str | None
    edited_by: Person | None
    reviewed_by: Person | None


class JobOut(BaseModel):
    id: uuid.UUID
    status: str
    done: int
    total: int
    progress: int
    current_step: str | None
    error: str | None


class Totals(BaseModel):
    cells: int
    done: int
    not_found: int
    failed: int
    queued: int
    unverified: int
    reviewed: int
    edited: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class TableSummary(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    title: str
    columns: int
    documents: int
    updated_at: datetime
    created_by: Person
    job: JobOut | None


class TableDetail(TableSummary):
    my_matter_role: str
    can_edit: bool
    column_list: list[ColumnOut]
    rows: list[RowOut]
    cells: list[CellOut]
    totals: Totals


class RunOut(BaseModel):
    job_id: uuid.UUID | None
    cells_queued: int
    documents: int
