import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["notice", "reply", "email", "memo", "other"]
Status = Literal["draft", "in_review", "changes_requested", "approved"]


class Person(BaseModel):
    id: uuid.UUID
    name: str


class DraftCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    kind: Kind = "other"
    content: str = Field(default="", max_length=200_000)
    from_message_id: uuid.UUID | None = Field(default=None, description="Start from an Assistant answer")


class DraftUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Kind | None = None


class VersionCreate(BaseModel):
    content: str = Field(max_length=200_000)
    base_version_no: int = Field(ge=1, description="The version you started editing from")
    note: str | None = Field(default=None, max_length=300)
    force: bool = Field(default=False, description="Save on top even if someone saved a newer version")


class VersionOut(BaseModel):
    version_no: int
    note: str | None
    created_by: Person
    created_at: datetime
    chars: int
    contributors: list[Person] = Field(default_factory=list, description="Everyone whose changes are in it")


class VersionContent(VersionOut):
    content: str


class ReviewOut(BaseModel):
    id: uuid.UUID
    version_no: int
    status: Literal["pending", "approved", "changes_requested", "withdrawn"]
    requested_by: Person
    reviewer: Person
    message: str | None
    decision_note: str | None
    decided_at: datetime | None
    created_at: datetime


class CommentOut(BaseModel):
    id: uuid.UUID
    version_no: int
    author: Person
    body: str
    quote: str | None
    resolved_by: Person | None
    resolved_at: datetime | None
    created_at: datetime


class PresenceOut(BaseModel):
    user: Person
    editing: bool
    seen_at: datetime


class ActivityOut(BaseModel):
    action: str
    actor: Person | None
    detail: dict[str, Any]
    at: datetime


class DraftSummary(BaseModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    title: str
    kind: Kind
    status: Status
    current_version_no: int
    approved_version_no: int | None
    updated_by: Person
    updated_at: datetime
    reviewer: Person | None = None  # pending approver, if any


class DraftDetail(DraftSummary):
    content: str
    sources: list[dict[str, Any]]
    approved_by: Person | None
    approved_at: datetime | None
    created_by: Person
    created_at: datetime
    my_matter_role: str
    can_edit: bool
    versions: list[VersionOut]
    reviews: list[ReviewOut]
    comments: list[CommentOut]
    present: list[PresenceOut]
    activity: list[ActivityOut]
    live: "LiveInfo"
    suggestions: list["SuggestionOut"]
    signatures: list["SignatureOut"] = Field(
        default_factory=list, description="E-signature envelopes, newest first"
    )


SignatureStatus = Literal["sent", "delivered", "completed", "declined", "voided", "failed"]
SignerStatus = Literal["created", "sent", "delivered", "signed", "declined", "voided"]


class SignerOut(BaseModel):
    name: str
    email: str
    routing_order: int
    status: SignerStatus
    signed_at: datetime | None
    declined_reason: str | None
    user: Person | None = Field(default=None, description="Set when the signer is a member of the firm")


class SignatureOut(BaseModel):
    id: uuid.UUID
    draft_id: uuid.UUID
    version_no: int = Field(description="The approved version that was sent; later edits never change it")
    provider: Literal["docusign", "fake"]
    envelope_id: str
    status: SignatureStatus
    subject: str
    message: str | None
    sent_by: Person
    sent_at: datetime
    completed_at: datetime | None
    voided_reason: str | None
    last_checked_at: datetime | None
    last_error: str | None
    signed_document_id: uuid.UUID | None = Field(description="Signed PDF, stored in the matter's Vault")
    certificate_document_id: uuid.UUID | None = Field(description="Provider's certificate of completion")
    signers: list[SignerOut]


class LiveInfo(BaseModel):
    """The shared working copy everyone types into (Step 2c)."""

    available: bool = Field(description="False where the server cannot run live editing; use versions only")
    unsaved_changes: bool = Field(description="The working copy differs from the current version")
    contributors: list[Person] = Field(description="Who changed the working copy since the current version")


class LiveSave(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class LiveText(BaseModel):
    text: str
    unsaved_changes: bool
    current_version_no: int


class Selection(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    start: int = Field(ge=0, description="Where the selection starts in the working copy (a hint)")


class SuggestionCreate(BaseModel):
    instruction: str = Field(
        min_length=3, max_length=1000, examples=["Tighten the indemnity and cite the SPA"]
    )
    selection: Selection | None = Field(default=None, description="Omit to work on the whole draft")
    use_documents: bool = Field(default=True, description="Ground the edit in this matter's documents")
    model: str = Field(default="auto", max_length=100)


SuggestionStatus = Literal["pending", "accepted", "rejected", "stale"]


class SuggestionOut(BaseModel):
    id: uuid.UUID
    requested_by: Person
    instruction: str
    scope: Literal["selection", "draft"]
    quote: str
    replacement: str
    explanation: str
    diff: list["DiffOp"]
    sources: list[dict[str, Any]]
    warnings: list[str]
    status: SuggestionStatus
    decided_by: Person | None
    decided_at: datetime | None
    model: str | None
    cost_usd: float
    created_at: datetime


class Head(BaseModel):
    """Small, frequent poll: is there a newer version, who else is here."""

    current_version_no: int
    status: Status
    updated_by: Person
    updated_at: datetime
    present: list[PresenceOut]


class PresenceIn(BaseModel):
    editing: bool = False


class ReviewRequest(BaseModel):
    reviewer_id: uuid.UUID
    message: str | None = Field(default=None, max_length=2000)


class ReviewDecision(BaseModel):
    decision: Literal["approve", "request_changes"]
    note: str | None = Field(default=None, max_length=2000)


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    quote: str | None = Field(default=None, max_length=2000)


class DiffOp(BaseModel):
    op: Literal["equal", "insert", "delete"]
    text: str


class DiffOut(BaseModel):
    from_version: int
    to_version: int
    ops: list[DiffOp]
    inserted_words: int
    deleted_words: int


DraftDetail.model_rebuild()
SuggestionOut.model_rebuild()
