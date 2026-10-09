import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UploadStatus = Literal["queued", "new_version", "duplicate", "rejected"]


class UploadedFile(BaseModel):
    filename: str
    status: UploadStatus
    document_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    version_no: int | None = None
    reason: str | None = None


class UploadResult(BaseModel):
    job_id: uuid.UUID | None
    files: list[UploadedFile]


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_id: uuid.UUID
    title: str
    doc_type: str
    status: str
    page_count: int | None
    ocr_pending_pages: list[int]
    error: str | None
    current_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int
    original_filename: str
    mime: str
    size_bytes: int
    sha256: str
    created_at: datetime


class DocumentDetail(DocumentOut):
    versions: list[VersionOut]
    chunk_count: int


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    ordinal: int
    section_path: str | None
    page_from: int
    page_to: int
    char_start: int
    char_end: int
    text: str
    ocr_confidence: float | None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    matter_ids: list[uuid.UUID] | None = None
    doc_types: list[str] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    matter_id: uuid.UUID
    doc_type: str
    section_path: str | None
    page_from: int
    page_to: int
    snippet: str
    score: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
