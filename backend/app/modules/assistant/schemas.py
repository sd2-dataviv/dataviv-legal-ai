import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["ask", "summarise", "draft"]
DraftKind = Literal["notice", "reply", "email", "memo"]


class ThreadCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ThreadOut(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    title: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class AskRequest(BaseModel):
    content: str = Field(min_length=2, max_length=4000)
    mode: Mode = "ask"
    draft_kind: DraftKind | None = None
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    model: str = Field(default="auto", max_length=100, examples=["auto", "openai/gpt-oss-120b"])


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    mode: Mode
    options: dict[str, Any]
    sources: list[dict[str, Any]]
    sentences: list[dict[str, Any]]
    verification: dict[str, Any]
    status: Literal["streaming", "complete", "failed"]
    error: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    cost_usd: float | None
    created_at: datetime


class ThreadDetail(ThreadOut):
    messages: list[MessageOut]


class ModelsOut(BaseModel):
    backend: str
    default: str
    allowed: list[str]
    routes: dict[str, str]
    catalogue: list[str]


class ModelPolicyIn(BaseModel):
    allowed: list[str] = Field(min_length=1)
    routes: dict[Literal["extract", "qa", "draft", "reason"], str] = {}
