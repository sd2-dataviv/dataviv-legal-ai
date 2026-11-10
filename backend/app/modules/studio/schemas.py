import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class FieldOut(BaseModel):
    key: str
    label: str
    hint: str
    required: bool
    long: bool


class TemplateOut(BaseModel):
    key: str
    category: str
    name: str
    description: str
    fields: list[FieldOut]


class GenerateRequest(BaseModel):
    template: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=300)
    inputs: dict[str, str] = Field(
        default_factory=dict, description="Answers to the template's fields, by key"
    )
    instructions: str = Field(default="", max_length=8000, description="Anything else the document must do")
    model: str | None = Field(default=None, max_length=200)

    @field_validator("inputs")
    @classmethod
    def _small(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 30:
            raise ValueError("at most 30 fields")
        for k, text in v.items():
            if len(k) > 40 or len(text) > 6000:
                raise ValueError(f"field {k[:40]!r} is too long (6,000 characters at most)")
        return v


class ReviseRequest(BaseModel):
    instructions: str = Field(min_length=1, max_length=8000, description="What to change")
    model: str | None = Field(default=None, max_length=200)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, max_length=200_000)


class Person(BaseModel):
    id: uuid.UUID
    name: str


class DocumentOut(BaseModel):
    id: uuid.UUID
    family_id: uuid.UUID
    parent_id: uuid.UUID | None
    template: str
    template_name: str
    category: str
    title: str
    summary: str
    status: str
    error: str | None
    model: str | None
    created_by: Person
    created_at: datetime
    updated_at: datetime
    versions: int = 1


class RevisionOut(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    instructions: str
    status: str
    created_by: Person
    created_at: datetime


class DocumentDetail(DocumentOut):
    inputs: dict[str, str]
    instructions: str
    content: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    cost_usd: Decimal | None
    revisions: list[RevisionOut]
