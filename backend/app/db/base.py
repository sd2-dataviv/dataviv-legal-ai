"""Declarative base. Tables are created by SQL migrations (backend/migrations/sql); these
models must mirror them (keep both in the same PR; a model-vs-schema check test lands in Step 1)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantMixin:
    """Every tenant table carries firm_id; Postgres RLS filters on it (ADR-002)."""

    firm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
