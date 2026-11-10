import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import DB, CurrentPrincipal
from app.db.session import tenant_session
from app.modules.studio import service
from app.modules.studio.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentUpdate,
    GenerateRequest,
    ReviseRequest,
    TemplateOut,
)

router = APIRouter(prefix="/v1/studio", tags=["drafting studio"])

_STREAM: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"text/event-stream": {}},
        "description": "meta, notice*, delta*, then done (the stored document) or error",
    }
}
_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(p: CurrentPrincipal) -> list[dict[str, Any]]:
    """Document types the studio can draft, grouped by category, with the facts each asks for."""
    service._staff(p)
    return service.templates()


@router.post("/documents", response_class=StreamingResponse, responses=_STREAM)
async def generate(body: GenerateRequest, p: CurrentPrincipal) -> StreamingResponse:
    """Draft a new document from a template, the lawyer's facts and instructions. No matter or source
    documents are used. Streams Server-Sent Events; validation errors come back as problem+json first."""
    async with tenant_session(p.firm_id) as db:  # committed before the stream starts
        prep = await service.prepare_generate(db, p, body)
    return StreamingResponse(service.run(prep), media_type="text/event-stream", headers=_HEADERS)


@router.post("/documents/{document_id}/revise", response_class=StreamingResponse, responses=_STREAM)
async def revise(document_id: uuid.UUID, body: ReviseRequest, p: CurrentPrincipal) -> StreamingResponse:
    """Write a new version of a finished document with the requested changes. The earlier version is kept."""
    async with tenant_session(p.firm_id) as db:
        prep = await service.prepare_revise(db, p, document_id, body)
    return StreamingResponse(service.run(prep), media_type="text/event-stream", headers=_HEADERS)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    p: CurrentPrincipal,
    db: DB,
    q: Annotated[str | None, Query(max_length=200, description="Search titles and text")] = None,
    template: Annotated[str | None, Query(max_length=40)] = None,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[DocumentOut]:
    """The firm's studio documents, newest first; one row per document showing its latest version."""
    return await service.list_documents(db, p, q, template, mine, limit)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> DocumentDetail:
    return await service.detail(db, p, document_id)


@router.patch("/documents/{document_id}", response_model=DocumentDetail)
async def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, p: CurrentPrincipal, db: DB
) -> DocumentDetail:
    """Rename, or save the lawyer's own edits to the text. Author, partners and admins only."""
    return await service.update_document(db, p, document_id, body)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    """Remove the document and all its versions from the history (kept in the audit trail)."""
    await service.delete_document(db, p, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/export")
async def export(document_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    """The document as Word (.docx): placeholders highlighted, drafting notes at the end."""
    data, filename = await service.export_docx(db, p, document_id)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
