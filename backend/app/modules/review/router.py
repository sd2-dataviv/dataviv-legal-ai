import uuid
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, CurrentPrincipal
from app.modules.review import service
from app.modules.review.schemas import (
    CellPatch,
    ColumnIn,
    ColumnPatch,
    DocumentsIn,
    RunIn,
    RunOut,
    TableCreate,
    TableDetail,
    TablePatch,
    TableSummary,
)

router = APIRouter(tags=["review tables"])


@router.get("/v1/review-templates")
async def list_templates(p: CurrentPrincipal) -> list[dict[str, Any]]:
    """Ready-made column sets: contracts, litigation file, notices."""
    return service.templates()


@router.post(
    "/v1/matters/{matter_id}/review-tables", response_model=TableDetail, status_code=status.HTTP_201_CREATED
)
async def create_table(matter_id: uuid.UUID, body: TableCreate, p: CurrentPrincipal, db: DB) -> TableDetail:
    """Columns from a template and/or your own; rows default to every indexed document in the matter."""
    return await service.create_table(db, p, matter_id, body)


@router.get("/v1/matters/{matter_id}/review-tables", response_model=list[TableSummary])
async def list_tables(matter_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> list[TableSummary]:
    return await service.list_tables(db, p, matter_id)


@router.get("/v1/review-tables/{table_id}", response_model=TableDetail)
async def get_table(table_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> TableDetail:
    """The grid: columns, rows (documents), every cell with its answer, quote, pages and checks."""
    return await service.detail(db, p, table_id)


@router.patch("/v1/review-tables/{table_id}", response_model=TableDetail)
async def rename(table_id: uuid.UUID, body: TablePatch, p: CurrentPrincipal, db: DB) -> TableDetail:
    return await service.rename(db, p, table_id, body)


@router.delete("/v1/review-tables/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(table_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    await service.delete_table(db, p, table_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/review-tables/{table_id}/columns", response_model=TableDetail, status_code=status.HTTP_201_CREATED
)
async def add_column(table_id: uuid.UUID, body: ColumnIn, p: CurrentPrincipal, db: DB) -> TableDetail:
    return await service.add_column(db, p, table_id, body)


@router.patch("/v1/review-tables/{table_id}/columns/{column_id}", response_model=TableDetail)
async def patch_column(
    table_id: uuid.UUID, column_id: uuid.UUID, body: ColumnPatch, p: CurrentPrincipal, db: DB
) -> TableDetail:
    """Changing the question, type or choices clears that column's answers (they answered something else)."""
    return await service.patch_column(db, p, table_id, column_id, body)


@router.delete("/v1/review-tables/{table_id}/columns/{column_id}", response_model=TableDetail)
async def delete_column(
    table_id: uuid.UUID, column_id: uuid.UUID, p: CurrentPrincipal, db: DB
) -> TableDetail:
    return await service.delete_column(db, p, table_id, column_id)


@router.post("/v1/review-tables/{table_id}/documents", response_model=TableDetail)
async def add_documents(table_id: uuid.UUID, body: DocumentsIn, p: CurrentPrincipal, db: DB) -> TableDetail:
    return await service.add_documents(db, p, table_id, body)


@router.delete("/v1/review-tables/{table_id}/documents/{document_id}", response_model=TableDetail)
async def remove_document(
    table_id: uuid.UUID, document_id: uuid.UUID, p: CurrentPrincipal, db: DB
) -> TableDetail:
    return await service.remove_document(db, p, table_id, document_id)


@router.post("/v1/review-tables/{table_id}/run", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def run(table_id: uuid.UUID, body: RunIn, p: CurrentPrincipal, db: DB) -> RunOut:
    """Fill cells in the background (one model call per document). Poll `GET /v1/jobs/{job_id}`."""
    return await service.run(db, p, table_id, body)


@router.post("/v1/review-tables/{table_id}/cancel", response_model=TableDetail)
async def cancel(table_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> TableDetail:
    return await service.cancel(db, p, table_id)


@router.patch("/v1/review-tables/{table_id}/cells/{cell_id}", response_model=TableDetail)
async def patch_cell(
    table_id: uuid.UUID, cell_id: uuid.UUID, body: CellPatch, p: CurrentPrincipal, db: DB
) -> TableDetail:
    """Correct an answer (the model's stays on record), revert a correction, or mark the cell reviewed."""
    return await service.patch_cell(db, p, table_id, cell_id, body)


@router.get("/v1/review-tables/{table_id}/export")
async def export(
    table_id: uuid.UUID,
    p: CurrentPrincipal,
    db: DB,
    fmt: Annotated[Literal["csv", "xlsx"], Query(alias="format")] = "xlsx",
) -> Response:
    """Excel (answers + an Evidence sheet with quotes and checks) or CSV."""
    data, filename, mime = await service.export(db, p, table_id, fmt)
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
