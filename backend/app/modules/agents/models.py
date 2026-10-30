import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin


class AgentDef(TenantMixin, Base):
    __tablename__ = "agent_defs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(TenantMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    slug: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    goal: Mapped[str] = mapped_column(Text)
    scope_document_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    status: Mapped[str] = mapped_column(String, default="queued")
    max_steps: Mapped[int] = mapped_column(Integer)
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    sentences: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"))
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentStep(TenantMixin, Base):
    __tablename__ = "agent_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    position: Mapped[int] = mapped_column(Integer)
    thought: Mapped[str | None] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String)
    observation: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryItem(TenantMixin, Base):
    __tablename__ = "matter_memory"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    kind: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunks.id"))
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String, default="user")
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
