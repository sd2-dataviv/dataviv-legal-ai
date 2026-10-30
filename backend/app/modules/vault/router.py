import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.deps import DB, CurrentPrincipal
from app.modules.vault import service
from app.modules.vault.schemas import (
    ChunkOut,
    DocumentDetail,
    DocumentOut,
    SearchRequest,
    SearchResponse,
    SearchStatus,
    UploadResult,
    VersionOut,
)

router = APIRouter(tags=["vault"])


@router.post(
    "/v1/matters/{matter_id}/documents", response_model=UploadResult, status_code=status.HTTP_202_ACCEPTED
)
async def upload_documents(
    matter_id: uuid.UUID,
    p: CurrentPrincipal,
    db: DB,
    files: Annotated[list[UploadFile], File(description="One or more files")],
) -> UploadResult:
    """Upload files into a matter. Processing runs in the background: poll `GET /v1/jobs/{job_id}`."""
    return await service.upload(db, p, matter_id, files)


@router.get("/v1/matters/{matter_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    matter_id: uuid.UUID,
    p: CurrentPrincipal,
    db: DB,
    doc_type: Annotated[str | None, Query()] = None,
    doc_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[DocumentOut]:
    docs = await service.list_documents(db, p, matter_id, doc_type, doc_status)
    return [DocumentOut.model_validate(d) for d in docs]


@router.get("/v1/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> DocumentDetail:
    doc, versions, n, semantic = await service.document_detail(db, p, document_id)
    return DocumentDetail(
        **DocumentOut.model_validate(doc).model_dump(),
        versions=[VersionOut.model_validate(v) for v in versions],
        chunk_count=n,
        semantic_chunks=semantic,
    )


@router.get("/v1/documents/{document_id}/download")
async def download_document(document_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    data, mime, filename = await service.download(db, p, document_id)
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/v1/documents/{document_id}/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(document_id: uuid.UUID, chunk_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> ChunkOut:
    """Citation target: the exact passage, its pages and character span."""
    return ChunkOut.model_validate(await service.get_chunk(db, p, document_id, chunk_id))


@router.delete("/v1/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    await service.delete_document(db, p, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/v1/search", response_model=SearchResponse)
async def search(body: SearchRequest, p: CurrentPrincipal, db: DB) -> SearchResponse:
    """Search every matter you can access (or a subset) by words and by meaning, with highlighted snippets.

    Each hit says how it matched (`keyword`, `semantic` or `both`). If semantic search is down, results
    are keyword matches and `notice` says so."""
    return await service.search(db, p, body)


@router.get("/v1/search/status", response_model=SearchStatus)
async def search_status(p: CurrentPrincipal, db: DB) -> SearchStatus:
    """Is semantic search on and reachable, which model, and how many of your firm's passages wait to be embedded."""
    return await service.search_status(db, p)
