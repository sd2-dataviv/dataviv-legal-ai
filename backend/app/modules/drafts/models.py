import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin


class Draft(TenantMixin, Base):
    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String, default="other")
    status: Mapped[str] = mapped_column(String, default="draft")
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    approved_version_no: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    updated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DraftVersion(TenantMixin, Base):
    __tablename__ = "draft_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # everyone whose typing or accepted AI suggestion went into this version (Step 2c)
    contributors: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class DraftReview(TenantMixin, Base):
    __tablename__ = "draft_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String, default="pending")
    message: Mapped[str | None] = mapped_column(Text)
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class DraftComment(TenantMixin, Base):
    __tablename__ = "draft_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class DraftPresence(TenantMixin, Base):
    __tablename__ = "draft_presence"

    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    editing: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DraftLive(TenantMixin, Base):
    """The shared working copy as a Yjs document (Step 2c, ADR-020). Versions remain the record."""

    __tablename__ = "draft_live"

    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"), primary_key=True)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    state: Mapped[bytes] = mapped_column(LargeBinary)
    contributors: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DraftSuggestion(TenantMixin, Base):
    """An AI-proposed change shown as a redline until a person accepts or rejects it."""

    __tablename__ = "draft_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"))
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    instruction: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String)
    quote: Mapped[str] = mapped_column(Text)
    start_hint: Mapped[int] = mapped_column(Integer)
    replacement: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
