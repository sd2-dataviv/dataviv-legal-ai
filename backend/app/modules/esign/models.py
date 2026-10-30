import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin


class SignatureRequest(TenantMixin, Base):
    """One envelope sent to the signing provider for one approved version of a draft."""

    __tablename__ = "signature_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))
    draft_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("drafts.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String)
    envelope_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="sent")
    subject: Mapped[str] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    sent_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_reason: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    signed_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id")
    )
    certificate_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SignatureSigner(TenantMixin, Base):
    __tablename__ = "signature_signers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("signature_requests.id"))
    recipient_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    routing_order: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String, default="sent")
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_reason: Mapped[str | None] = mapped_column(Text)
