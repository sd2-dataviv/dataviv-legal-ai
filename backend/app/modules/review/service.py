"""Review tables (VLT-09, VLT-10): the same questions asked of many documents, one cited answer per cell.

Members read and export; lead/partner/member roles build tables, run them and correct cells
(viewers only read). A person's correction never gets overwritten by a later run: the model's
answer is kept alongside it and the cell is flagged if the two differ.
"""

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.models import Job, User
from app.core.permissions import EDIT_ROLES, require_matter_access
from app.core.security import Principal
from app.modules.review.models import ReviewCell, ReviewColumn, ReviewRow, ReviewTable
from app.modules.review.normalise import normalise_value
from app.modules.review.schemas import (
    AnswerType,
    CellOut,
    CellPatch,
    CellStatus,
    ColumnIn,
    ColumnOut,
    ColumnPatch,
    DocumentsIn,
    JobOut,
    Person,
    RowOut,
    RunIn,
    RunOut,
    TableCreate,
    TableDetail,
    TablePatch,
    TableSummary,
    Totals,
)
from app.modules.review.templates import TEMPLATES, ColumnSpec
from app.modules.review.xlsx import build_xlsx
from app.modules.vault.models import Document

ACTIVE = ("queued", "running")


# --------------------------------------------------------------------------- helpers


async def _table(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, *, edit: bool = False
) -> tuple[ReviewTable, str]:
    table = await db.get(ReviewTable, table_id)
    if table is None:
        raise NotFound("Review table not found")
    try:
        role = await require_matter_access(db, p, table.matter_id)
    except NotFound:
        raise NotFound("Review table not found") from None
    if edit and role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter can read and export review tables, but not change them")
    return table, role


async def _names(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {r[0]: r[1] for r in (await db.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()}


def _job_out(job: Job | None) -> JobOut | None:
    if job is None:
        return None
    return JobOut(
        id=job.id,
        status=job.status,
        done=job.done,
        total=job.total,
        progress=job.progress,
        current_step=job.current_step,
        error=job.error,
    )


async def _audit(
    db: AsyncSession, p: Principal, action: str, table: ReviewTable, detail: dict[str, Any]
) -> None:
    await audit.record(db, p, action, "review_table", table.id, table.matter_id, detail)


def _touch(table: ReviewTable) -> None:
    table.updated_at = datetime.now(UTC)


async def _matter_documents(
    db: AsyncSession, matter_id: uuid.UUID, ids: list[uuid.UUID] | None
) -> list[uuid.UUID]:
    stmt = select(Document.id).where(Document.matter_id == matter_id, Document.deleted_at.is_(None))
    if ids is None:
        stmt = stmt.where(Document.status.in_(("indexed", "partial")))
    else:
        stmt = stmt.where(Document.id.in_(ids))
    found = list((await db.scalars(stmt.order_by(Document.created_at).limit(10_000))).all())
    if ids is not None and len(set(found)) != len(set(ids)):
        raise NotFound("One or more documents are not in this matter")
    if ids is not None:  # keep the caller's order
        order = {d: i for i, d in enumerate(ids)}
        found.sort(key=lambda d: order[d])
    return found


async def _add_columns(db: AsyncSession, p: Principal, table: ReviewTable, specs: list[ColumnSpec]) -> None:
    last = await db.scalar(select(func.max(ReviewColumn.position)).where(ReviewColumn.table_id == table.id))
    start = -1 if last is None else int(last)
    for i, c in enumerate(specs):
        if c["answer_type"] == "choice" and not c["choices"]:
            raise Unprocessable(f"Column {c['label']!r} needs at least one choice")
        db.add(
            ReviewColumn(
                firm_id=p.firm_id,
                matter_id=table.matter_id,
                table_id=table.id,
                position=start + 1 + i,
                label=c["label"].strip(),
                question=c["question"].strip(),
                answer_type=c["answer_type"],
                choices=[x.strip() for x in c["choices"] if x.strip()],
                created_by=p.user_id,
            )
        )


async def _add_rows(db: AsyncSession, p: Principal, table: ReviewTable, doc_ids: list[uuid.UUID]) -> int:
    existing = set(
        (await db.scalars(select(ReviewRow.document_id).where(ReviewRow.table_id == table.id))).all()
    )
    start = (
        await db.scalar(select(func.count()).select_from(ReviewRow).where(ReviewRow.table_id == table.id))
    ) or 0
    new = [d for d in doc_ids if d not in existing]
    for i, d in enumerate(new):
        db.add(
            ReviewRow(
                firm_id=p.firm_id,
                matter_id=table.matter_id,
                table_id=table.id,
                document_id=d,
                position=start + i,
            )
        )
    return len(new)


def _spec(c: ColumnIn) -> ColumnSpec:
    return {"label": c.label, "question": c.question, "answer_type": c.answer_type, "choices": c.choices}


# --------------------------------------------------------------------------- tables


def templates() -> list[dict[str, Any]]:
    return [{"id": k, **v} for k, v in TEMPLATES.items()]


async def create_table(
    db: AsyncSession, p: Principal, matter_id: uuid.UUID, body: TableCreate
) -> TableDetail:
    role = await require_matter_access(db, p, matter_id)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot create review tables")
    template = TEMPLATES[body.template] if body.template else None
    specs: list[ColumnSpec] = list(cast(list[ColumnSpec], template["columns"])) if template else []
    specs += [_spec(c) for c in body.columns]
    if not specs:
        raise Unprocessable("Add at least one column, or start from a template")
    title = (body.title or (str(template["title"]) if template else "") or "Review table").strip()
    table = ReviewTable(firm_id=p.firm_id, matter_id=matter_id, title=title[:200], created_by=p.user_id)
    db.add(table)
    await db.flush()
    await _add_columns(db, p, table, specs)
    docs = await _matter_documents(db, matter_id, body.document_ids)
    await _add_rows(db, p, table, docs)
    await db.flush()
    await _audit(
        db,
        p,
        "review.create",
        table,
        {"title": title, "template": body.template, "columns": len(specs), "documents": len(docs)},
    )
    return await detail(db, p, table.id)


async def list_tables(db: AsyncSession, p: Principal, matter_id: uuid.UUID) -> list[TableSummary]:
    await require_matter_access(db, p, matter_id)
    tables = list(
        (
            await db.scalars(
                select(ReviewTable)
                .where(ReviewTable.matter_id == matter_id)
                .order_by(ReviewTable.updated_at.desc())
            )
        ).all()
    )
    if not tables:
        return []
    ids = [t.id for t in tables]
    ncols: dict[uuid.UUID, int] = {
        r[0]: r[1]
        for r in await db.execute(
            select(ReviewColumn.table_id, func.count())
            .where(ReviewColumn.table_id.in_(ids))
            .group_by(ReviewColumn.table_id)
        )
    }
    nrows: dict[uuid.UUID, int] = {
        r[0]: r[1]
        for r in await db.execute(
            select(ReviewRow.table_id, func.count())
            .where(ReviewRow.table_id.in_(ids))
            .group_by(ReviewRow.table_id)
        )
    }
    jobs = {
        j.id: j
        for j in (
            await db.scalars(select(Job).where(Job.id.in_([t.job_id for t in tables if t.job_id])))
        ).all()
    }
    names = await _names(db, {t.created_by for t in tables})
    return [
        TableSummary(
            id=t.id,
            matter_id=t.matter_id,
            title=t.title,
            columns=ncols.get(t.id, 0),
            documents=nrows.get(t.id, 0),
            updated_at=t.updated_at,
            created_by=Person(id=t.created_by, name=names.get(t.created_by, "Unknown user")),
            job=_job_out(jobs.get(t.job_id) if t.job_id else None),
        )
        for t in tables
    ]


async def _ensure_cells(db: AsyncSession, p: Principal, table: ReviewTable) -> None:
    """Every row and column pair has a cell (new rows and columns start empty)."""
    cols = list((await db.scalars(select(ReviewColumn.id).where(ReviewColumn.table_id == table.id))).all())
    docs = list((await db.scalars(select(ReviewRow.document_id).where(ReviewRow.table_id == table.id))).all())
    values = [
        {
            "firm_id": p.firm_id,
            "matter_id": table.matter_id,
            "table_id": table.id,
            "column_id": c,
            "document_id": d,
        }
        for c in cols
        for d in docs
    ]
    for i in range(0, len(values), 1000):
        await db.execute(
            pg_insert(ReviewCell)
            .values(values[i : i + 1000])
            .on_conflict_do_nothing(index_elements=["column_id", "document_id"])
        )


async def detail(db: AsyncSession, p: Principal, table_id: uuid.UUID) -> TableDetail:
    table, role = await _table(db, p, table_id)
    columns = list(
        (
            await db.scalars(
                select(ReviewColumn).where(ReviewColumn.table_id == table.id).order_by(ReviewColumn.position)
            )
        ).all()
    )
    rows = (
        await db.execute(
            select(
                ReviewRow.document_id, Document.title, Document.doc_type, Document.status, Document.page_count
            )
            .join(Document, Document.id == ReviewRow.document_id)
            .where(ReviewRow.table_id == table.id, Document.deleted_at.is_(None))
            .order_by(ReviewRow.position)
        )
    ).all()
    live_docs = {r[0] for r in rows}
    cells = [
        c
        for c in (await db.scalars(select(ReviewCell).where(ReviewCell.table_id == table.id))).all()
        if c.document_id in live_docs
    ]
    job = await db.get(Job, table.job_id) if table.job_id else None
    names = await _names(
        db, {table.created_by} | {x for c in cells for x in (c.edited_by, c.reviewed_by) if x}
    )

    def person(uid: uuid.UUID | None) -> Person | None:
        return Person(id=uid, name=names.get(uid, "Unknown user")) if uid else None

    status = [c.status for c in cells]
    totals = Totals(
        cells=len(rows) * len(columns),
        done=status.count("done"),
        not_found=status.count("not_found"),
        failed=status.count("failed"),
        queued=status.count("queued"),
        unverified=sum(1 for c in cells if c.status == "done" and c.verified is False),
        reviewed=sum(1 for c in cells if c.reviewed_by),
        edited=sum(1 for c in cells if c.edited_by),
        input_tokens=sum(c.input_tokens or 0 for c in cells),
        output_tokens=sum(c.output_tokens or 0 for c in cells),
        cost_usd=round(float(sum(c.cost_usd or 0 for c in cells)), 6),
    )
    return TableDetail(
        id=table.id,
        matter_id=table.matter_id,
        title=table.title,
        columns=len(columns),
        documents=len(rows),
        updated_at=table.updated_at,
        created_by=cast(Person, person(table.created_by)),
        job=_job_out(job),
        my_matter_role=role,
        can_edit=role in EDIT_ROLES,
        column_list=[
            ColumnOut(
                id=c.id,
                position=c.position,
                label=c.label,
                question=c.question,
                answer_type=cast(AnswerType, c.answer_type),
                choices=list(c.choices or []),
            )
            for c in columns
        ],
        rows=[
            RowOut(document_id=r[0], title=r[1], doc_type=r[2], status=r[3], page_count=r[4]) for r in rows
        ],
        cells=[
            CellOut(
                id=c.id,
                column_id=c.column_id,
                document_id=c.document_id,
                status=cast(CellStatus, c.status),
                answer=c.answer,
                model_answer=c.model_answer,
                value=c.value,
                quote=c.quote,
                chunk_id=c.chunk_id,
                page_from=c.page_from,
                page_to=c.page_to,
                verified=c.verified,
                flag=c.flag,
                error=c.error,
                edited_by=person(c.edited_by),
                reviewed_by=person(c.reviewed_by),
            )
            for c in cells
        ],
        totals=totals,
    )


async def _not_running(db: AsyncSession, table: ReviewTable) -> None:
    if table.job_id:
        job = await db.get(Job, table.job_id)
        if job is not None and job.status in ACTIVE:
            raise Conflict("This table is being filled in. Wait for it to finish or cancel the run first.")


async def rename(db: AsyncSession, p: Principal, table_id: uuid.UUID, body: TablePatch) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    table.title = body.title.strip()
    _touch(table)
    await _audit(db, p, "review.rename", table, {"title": table.title})
    return await detail(db, p, table_id)


async def delete_table(db: AsyncSession, p: Principal, table_id: uuid.UUID) -> None:
    table, _ = await _table(db, p, table_id, edit=True)
    await _not_running(db, table)
    await _audit(db, p, "review.delete", table, {"title": table.title})
    await db.delete(table)


# --------------------------------------------------------------------------- columns and rows


async def add_column(db: AsyncSession, p: Principal, table_id: uuid.UUID, body: ColumnIn) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    await _not_running(db, table)
    await _add_columns(db, p, table, [_spec(body)])
    _touch(table)
    await db.flush()
    await _audit(db, p, "review.column_add", table, {"label": body.label})
    return await detail(db, p, table_id)


async def _column(db: AsyncSession, table: ReviewTable, column_id: uuid.UUID) -> ReviewColumn:
    column = await db.get(ReviewColumn, column_id)
    if column is None or column.table_id != table.id:
        raise NotFound("Column not found")
    return column


async def patch_column(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, column_id: uuid.UUID, body: ColumnPatch
) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    await _not_running(db, table)
    column = await _column(db, table, column_id)
    changed_meaning = False
    if body.label is not None:
        column.label = body.label.strip()
    for field in ("question", "answer_type", "choices"):
        new = getattr(body, field)
        if new is not None and new != getattr(column, field):
            setattr(column, field, new.strip() if isinstance(new, str) else new)
            changed_meaning = True
    if column.answer_type == "choice" and not column.choices:
        raise Unprocessable("A choice column needs at least one choice")
    if changed_meaning:  # old answers (and corrections) answered a different question
        await db.execute(delete(ReviewCell).where(ReviewCell.column_id == column.id))
    _touch(table)
    await db.flush()
    await _audit(
        db, p, "review.column_edit", table, {"column_id": str(column.id), "cleared": changed_meaning}
    )
    return await detail(db, p, table_id)


async def delete_column(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, column_id: uuid.UUID
) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    await _not_running(db, table)
    column = await _column(db, table, column_id)
    await _audit(db, p, "review.column_delete", table, {"label": column.label})
    await db.delete(column)
    _touch(table)
    await db.flush()
    return await detail(db, p, table_id)


async def add_documents(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, body: DocumentsIn
) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    docs = await _matter_documents(db, table.matter_id, body.document_ids)
    added = await _add_rows(db, p, table, docs)
    _touch(table)
    await db.flush()
    await _audit(db, p, "review.documents_add", table, {"added": added})
    return await detail(db, p, table_id)


async def remove_document(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, document_id: uuid.UUID
) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    await _not_running(db, table)
    await db.execute(
        delete(ReviewCell).where(ReviewCell.table_id == table.id, ReviewCell.document_id == document_id)
    )
    await db.execute(
        delete(ReviewRow).where(ReviewRow.table_id == table.id, ReviewRow.document_id == document_id)
    )
    _touch(table)
    await _audit(db, p, "review.document_remove", table, {"document_id": str(document_id)})
    return await detail(db, p, table_id)


# --------------------------------------------------------------------------- running


async def run(db: AsyncSession, p: Principal, table_id: uuid.UUID, body: RunIn) -> RunOut:
    table, _ = await _table(db, p, table_id, edit=True)
    await _not_running(db, table)
    await _ensure_cells(db, p, table)
    stmt = select(ReviewCell).where(ReviewCell.table_id == table.id)
    if body.scope == "missing":
        stmt = stmt.where(ReviewCell.status.in_(("empty", "failed", "queued")))
    if body.column_ids:
        stmt = stmt.where(ReviewCell.column_id.in_(body.column_ids))
    if body.document_ids:
        stmt = stmt.where(ReviewCell.document_id.in_(body.document_ids))
    cells = list((await db.scalars(stmt)).all())
    order = {
        d: i
        for i, d in enumerate(
            (
                await db.scalars(
                    select(ReviewRow.document_id)
                    .where(ReviewRow.table_id == table.id)
                    .order_by(ReviewRow.position)
                )
            ).all()
        )
    }
    cells = [c for c in cells if c.document_id in order]
    if not cells:
        return RunOut(job_id=None, cells_queued=0, documents=0)
    for c in cells:
        c.status, c.error = "queued", None
    docs = sorted({c.document_id for c in cells}, key=lambda d: order[d])
    job = Job(
        firm_id=p.firm_id,
        matter_id=table.matter_id,
        kind="review_table",
        status="queued",
        total=len(docs),
        done=0,
        created_by=p.user_id,
        current_step=f"Waiting to read {len(docs)} document(s)",
        payload={"table_id": str(table.id), "document_ids": [str(d) for d in docs], "failed": []},
    )
    db.add(job)
    await db.flush()
    table.job_id = job.id
    _touch(table)
    await _audit(
        db,
        p,
        "review.run",
        table,
        {"job_id": str(job.id), "cells": len(cells), "documents": len(docs), "scope": body.scope},
    )
    return RunOut(job_id=job.id, cells_queued=len(cells), documents=len(docs))


async def cancel(db: AsyncSession, p: Principal, table_id: uuid.UUID) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    job = await db.get(Job, table.job_id) if table.job_id else None
    if job is None or job.status not in ACTIVE:
        raise Conflict("Nothing is running for this table")
    job.status, job.error, job.current_step = (
        "cancelled",
        f"Cancelled after {job.done} of {job.total} documents",
        "Cancelled",
    )
    queued = list(
        (
            await db.scalars(
                select(ReviewCell).where(ReviewCell.table_id == table.id, ReviewCell.status == "queued")
            )
        ).all()
    )
    for c in queued:
        c.status = "empty"
    await _audit(db, p, "review.cancel", table, {"job_id": str(job.id), "done": job.done, "total": job.total})
    return await detail(db, p, table_id)


async def patch_cell(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, cell_id: uuid.UUID, body: CellPatch
) -> TableDetail:
    table, _ = await _table(db, p, table_id, edit=True)
    cell = await db.get(ReviewCell, cell_id)
    if cell is None or cell.table_id != table.id:
        raise NotFound("Cell not found")
    column = await _column(db, table, cell.column_id)
    now = datetime.now(UTC)
    action: Literal["review.cell_edit", "review.cell_revert", "review.cell_reviewed"] | None = None
    if body.revert:
        cell.answer, cell.edited_by, cell.edited_at = cell.model_answer, None, None
        cell.value, _ = normalise_value(
            column.answer_type, None, cell.answer or "", list(column.choices or [])
        )
        action = "review.cell_revert"
    elif body.answer is not None:
        cell.answer, cell.edited_by, cell.edited_at = body.answer.strip(), p.user_id, now
        cell.value, _ = normalise_value(column.answer_type, None, cell.answer, list(column.choices or []))
        if cell.status in ("empty", "failed", "queued"):
            cell.status = "done"
        action = "review.cell_edit"
    if body.reviewed is not None:
        cell.reviewed_by, cell.reviewed_at = (p.user_id, now) if body.reviewed else (None, None)
        action = action or "review.cell_reviewed"
    if action is None:
        raise Unprocessable("Nothing to change")
    cell.updated_at = now
    await db.flush()
    await _audit(db, p, action, table, {"cell_id": str(cell.id), "reviewed": body.reviewed})
    return await detail(db, p, table_id)


# --------------------------------------------------------------------------- export


def _pages(c: CellOut) -> str:
    if c.page_from is None:
        return ""
    return f"p. {c.page_from}" if c.page_from == c.page_to else f"pp. {c.page_from}-{c.page_to}"


def _shown(c: CellOut | None) -> str:
    if c is None or c.status == "empty":
        return ""
    if c.status == "queued":
        return "(being read)"
    if c.status == "failed":
        return f"(failed: {c.error or 'unknown error'})"
    return c.answer or ("Not stated" if c.status == "not_found" else "")


def _source(c: CellOut | None) -> str:
    if c is None or c.status != "done":
        return ""
    parts = [_pages(c)]
    if c.edited_by:
        parts.append(f"corrected by {c.edited_by.name}")
    elif c.verified:
        parts.append("verified")
    else:
        parts.append(f"CHECK: {c.flag or 'not verified'}")
    if c.reviewed_by:
        parts.append(f"reviewed by {c.reviewed_by.name}")
    return " · ".join(x for x in parts if x)


async def export(
    db: AsyncSession, p: Principal, table_id: uuid.UUID, fmt: Literal["csv", "xlsx"]
) -> tuple[bytes, str, str]:
    d = await detail(db, p, table_id)
    grid = {(c.document_id, c.column_id): c for c in d.cells}
    header = ["Document", "Type"] + [x for col in d.column_list for x in (col.label, f"{col.label} - source")]
    rows = [header]
    for r in d.rows:
        line = [r.title, r.doc_type]
        for col in d.column_list:
            c = grid.get((r.document_id, col.id))
            line += [_shown(c), _source(c)]
        rows.append(line)
    table, _ = await _table(db, p, table_id)
    await _audit(
        db,
        p,
        "review.export",
        table,
        {"format": fmt, "documents": len(d.rows), "columns": len(d.column_list)},
    )
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in d.title)[:60].strip() or "review"
    if fmt == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        return (
            buf.getvalue().encode("utf-8-sig"),
            f"{safe}.csv",
            "text/csv; charset=utf-8",
        )  # BOM: Excel reads Hindi correctly
    labels = {col.id: col for col in d.column_list}
    titles = {r.document_id: r.title for r in d.rows}
    evidence = [
        [
            "Document",
            "Column",
            "Question",
            "Answer",
            "Normalised value",
            "Quote",
            "Pages",
            "Verified",
            "Flag",
            "Corrected by",
            "Reviewed by",
        ]
    ]
    for c in sorted(
        d.cells,
        key=lambda c: (
            titles.get(c.document_id, ""),
            labels[c.column_id].position if c.column_id in labels else 0,
        ),
    ):
        if c.column_id not in labels or c.status == "empty":
            continue
        col = labels[c.column_id]
        evidence.append(
            [
                titles.get(c.document_id, ""),
                col.label,
                col.question,
                _shown(c),
                c.value or "",
                c.quote or "",
                _pages(c),
                "yes" if c.verified else ("no" if c.status == "done" else ""),
                c.flag or "",
                c.edited_by.name if c.edited_by else "",
                c.reviewed_by.name if c.reviewed_by else "",
            ]
        )
    widths = [34, 14] + [w for _ in d.column_list for w in (36, 22)]
    data = build_xlsx(
        [("Review", rows, widths), ("Evidence", evidence, [30, 18, 40, 40, 18, 60, 10, 9, 40, 18, 18])]
    )
    return data, f"{safe}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
