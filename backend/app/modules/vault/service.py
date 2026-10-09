"""Vault business logic: upload, list, detail, download, delete, citation lookup, search."""

import hashlib
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import Text, bindparam, delete, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.errors import AppError, Forbidden, NotFound
from app.core.models import Job
from app.core.permissions import accessible_matter_ids, require_matter_access
from app.core.security import Principal
from app.core.storage import content_key, get_storage
from app.modules.vault.extract import UnsupportedFile, mime_for
from app.modules.vault.models import Chunk, Document, DocumentVersion
from app.modules.vault.schemas import SearchHit, SearchRequest, UploadedFile, UploadResult, UploadStatus

READ_ONLY_ROLES = frozenset({"viewer"})


class TooManyFiles(AppError):
    status = 413
    title = "Too many files"


def _clean_name(name: str | None) -> str:
    base = (name or "unnamed").replace("\\", "/").split("/")[-1].strip() or "unnamed"
    return re.sub(r"[\x00-\x1f]", "", base)[:255]


async def _spool(f: UploadFile, tmp_dir: Path, limit: int) -> tuple[Path, str, int]:
    """Stream an upload to a temp file, hashing as we go. Raises ValueError if over the size limit."""
    h, size = hashlib.sha256(), 0
    fd, tmp = tempfile.mkstemp(dir=tmp_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await f.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"larger than {limit // (1 << 20)} MB")
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp), h.hexdigest(), size


async def _document_for_write(db: AsyncSession, p: Principal, document_id: uuid.UUID) -> Document:
    doc = await db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise NotFound("Document not found")
    role = await require_matter_access(db, p, doc.matter_id)
    if role in READ_ONLY_ROLES:
        raise Forbidden("Viewers cannot change documents")
    return doc


async def get_document(db: AsyncSession, p: Principal, document_id: uuid.UUID) -> Document:
    doc = await db.get(Document, document_id)
    if doc is None or doc.deleted_at is not None:
        raise NotFound("Document not found")
    await require_matter_access(db, p, doc.matter_id)
    return doc


async def upload(db: AsyncSession, p: Principal, matter_id: uuid.UUID, files: list[UploadFile]) -> UploadResult:
    role = await require_matter_access(db, p, matter_id)
    if role in READ_ONLY_ROLES:
        raise Forbidden("Viewers cannot upload")
    s = get_settings()
    if len(files) > s.max_files_per_upload:
        raise TooManyFiles(f"At most {s.max_files_per_upload} files per upload")
    storage = get_storage()
    limit = s.max_upload_mb << 20
    results: list[UploadedFile] = []
    queued: list[uuid.UUID] = []

    for f in files:
        name = _clean_name(f.filename)
        try:
            mime = mime_for(name)
            tmp, sha, size = await _spool(f, storage.tmp_dir, limit)
        except (UnsupportedFile, ValueError) as exc:
            results.append(UploadedFile(filename=name, status="rejected", reason=str(exc)))
            continue

        dup = (
            await db.execute(
                select(DocumentVersion.document_id, DocumentVersion.id, DocumentVersion.version_no)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    DocumentVersion.matter_id == matter_id,
                    DocumentVersion.sha256 == sha,
                    Document.deleted_at.is_(None),
                )
                .limit(1)
            )
        ).first()
        if dup:
            tmp.unlink(missing_ok=True)
            results.append(
                UploadedFile(
                    filename=name, status="duplicate", document_id=dup[0], version_id=dup[1], version_no=dup[2]
                )
            )
            continue

        key = content_key(p.firm_id, matter_id, sha)
        storage.put_file(key, tmp)
        doc = (
            await db.scalars(
                select(Document).where(
                    Document.matter_id == matter_id,
                    Document.filename_key == name.lower(),
                    Document.deleted_at.is_(None),
                )
            )
        ).first()
        status: UploadStatus
        if doc is None:
            doc = Document(
                firm_id=p.firm_id, matter_id=matter_id, title=name, filename_key=name.lower(),
                created_by=p.user_id, status="queued",
            )
            db.add(doc)
            await db.flush()
            version_no, status = 1, "queued"
        else:
            last = await db.scalar(
                select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id)
            )
            version_no, status = (last or 0) + 1, "new_version"
            doc.status, doc.error = "queued", None
        version = DocumentVersion(
            firm_id=p.firm_id, matter_id=matter_id, document_id=doc.id, version_no=version_no,
            storage_key=key, sha256=sha, mime=mime, size_bytes=size, original_filename=name,
            created_by=p.user_id,
        )
        db.add(version)
        await db.flush()
        doc.current_version_id = version.id
        queued.append(version.id)
        results.append(
            UploadedFile(
                filename=name, status=status,
                document_id=doc.id, version_id=version.id, version_no=version_no,
            )
        )

    job_id: uuid.UUID | None = None
    if queued:
        job = Job(
            firm_id=p.firm_id, matter_id=matter_id, kind="ingest", status="queued", total=len(queued), done=0,
            created_by=p.user_id, payload={"version_ids": [str(v) for v in queued]},
            current_step=f"queued {len(queued)} file(s)",
        )
        db.add(job)
        await db.flush()
        job_id = job.id
    await audit.record(
        db, p, "document.upload", "matter", matter_id, matter_id,
        {"files": [r.model_dump(include={"filename", "status"}) for r in results], "job_id": job_id},
    )
    return UploadResult(job_id=job_id, files=results)


async def list_documents(
    db: AsyncSession, p: Principal, matter_id: uuid.UUID, doc_type: str | None, status: str | None
) -> list[Document]:
    await require_matter_access(db, p, matter_id)
    q = select(Document).where(Document.matter_id == matter_id, Document.deleted_at.is_(None))
    if doc_type:
        q = q.where(Document.doc_type == doc_type)
    if status:
        q = q.where(Document.status == status)
    return list((await db.scalars(q.order_by(Document.created_at.desc()))).all())


async def document_detail(
    db: AsyncSession, p: Principal, document_id: uuid.UUID
) -> tuple[Document, list[DocumentVersion], int]:
    doc = await get_document(db, p, document_id)
    versions = (
        await db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_no.desc())
        )
    ).all()
    n = await db.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id))
    return doc, list(versions), int(n or 0)


async def download(db: AsyncSession, p: Principal, document_id: uuid.UUID) -> tuple[bytes, str, str]:
    doc = await get_document(db, p, document_id)
    version = await db.get(DocumentVersion, doc.current_version_id) if doc.current_version_id else None
    if version is None:
        raise NotFound("No stored version")
    await audit.record(db, p, "document.download", "document", doc.id, doc.matter_id, {"version": version.version_no})
    return get_storage().read(version.storage_key), version.mime, version.original_filename


async def get_chunk(db: AsyncSession, p: Principal, document_id: uuid.UUID, chunk_id: uuid.UUID) -> Chunk:
    doc = await get_document(db, p, document_id)
    chunk = await db.get(Chunk, chunk_id)
    if chunk is None or chunk.document_id != doc.id:
        raise NotFound("Passage not found")
    return chunk


async def delete_document(db: AsyncSession, p: Principal, document_id: uuid.UUID) -> None:
    doc = await _document_for_write(db, p, document_id)
    doc.deleted_at = datetime.now(UTC)
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))  # gone from search immediately
    await audit.record(db, p, "document.delete", "document", doc.id, doc.matter_id, {"title": doc.title})


_SEARCH_SQL = text(
    """
    WITH q AS (
        SELECT websearch_to_tsquery('english', :query) || websearch_to_tsquery('simple', :query) AS tsq,
               websearch_to_tsquery('simple', :query) AS hq
    )
    SELECT c.id, c.document_id, d.title, c.matter_id, d.doc_type, c.section_path, c.page_from, c.page_to,
           ts_headline('simple', c.text, q.hq,
                       'MaxFragments=2, MaxWords=30, MinWords=12, FragmentDelimiter= … , StartSel=«, StopSel=»')
               AS snippet,
           ts_rank_cd(c.tsv, q.tsq, 32) AS score
      FROM chunks c
      JOIN documents d ON d.id = c.document_id AND d.deleted_at IS NULL
      CROSS JOIN q
     WHERE c.matter_id = ANY(:matter_ids)
       AND c.tsv @@ q.tsq
       AND (:doc_types IS NULL OR d.doc_type = ANY(:doc_types))
     ORDER BY score DESC, c.document_id, c.ordinal
     LIMIT :limit
    """
).bindparams(
    bindparam("matter_ids", type_=ARRAY(PGUUID(as_uuid=True))),
    bindparam("doc_types", type_=ARRAY(Text())),
)


async def search(db: AsyncSession, p: Principal, req: SearchRequest) -> list[SearchHit]:
    allowed = await accessible_matter_ids(db, p)
    scope = allowed if req.matter_ids is None else allowed & set(req.matter_ids)
    hits: list[SearchHit] = []
    if scope:
        rows = await db.execute(
            _SEARCH_SQL,
            {"query": req.query, "matter_ids": list(scope), "doc_types": req.doc_types, "limit": req.limit},
        )
        hits = [
            SearchHit(
                chunk_id=r[0], document_id=r[1], document_title=r[2], matter_id=r[3], doc_type=r[4],
                section_path=r[5], page_from=r[6], page_to=r[7], snippet=r[8], score=float(r[9]),
            )
            for r in rows
        ]
    await audit.record(
        db, p, "vault.search", "search", None, None,
        {"query": req.query, "matters": len(scope), "hits": len(hits)},
    )
    return hits
