"""Ingestion pipeline (VLT-03): one job = one upload batch; progress is reported per file (NFR-09).

Each file is processed in its own firm-scoped transaction, so one bad file never blocks the rest and the
job row shows live progress. Stages: load → extract → OCR → classify → chunk → index → embed.

The embed stage (Step 1c) runs after the passages are committed, so keyword search works at once even
if the embedding service is slow or down; passages it could not embed stay pending for
scripts/reindex_vectors.py and are visible in GET /v1/search/status.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core.embeddings import EmbeddingError
from app.core.models import Job
from app.core.storage import get_storage
from app.core.vectors import VectorStoreError
from app.db.session import tenant_session
from app.modules.vault import hybrid
from app.modules.vault.chunker import chunk_pages
from app.modules.vault.classify import classify
from app.modules.vault.extract import Page, UnsupportedFile, extract
from app.modules.vault.models import Chunk, Document, DocumentVersion
from app.modules.vault.ocr import OcrClient, OcrUnavailable, get_ocr_client

StepReporter = Callable[[str], Awaitable[None]]

log = logging.getLogger("dataviv.vault")


async def _set_step(firm_id: uuid.UUID, job_id: uuid.UUID, step: str) -> None:
    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is not None:
            job.current_step = step


async def _apply_ocr(
    pages: list[Page], data: bytes, version: DocumentVersion, ocr: OcrClient | None
) -> list[int]:
    """OCR the pages that need it. Returns page numbers still waiting for OCR."""
    wanted = [p.number for p in pages if p.needs_ocr]
    if not wanted:
        return []
    if ocr is None:
        return wanted
    try:
        results = {r.page: r for r in await ocr.recognise(data, version.original_filename, version.mime)}
    except OcrUnavailable as exc:
        log.warning("OCR failed for %s: %s", version.id, exc)
        return wanted
    pending = []
    for p in pages:
        if not p.needs_ocr:
            continue
        r = results.get(p.number)
        if r and r.text.strip():
            p.text, p.needs_ocr, p.ocr_confidence = r.text.strip(), False, r.confidence
        else:
            pending.append(p.number)
    return pending


async def ingest_version(
    firm_id: uuid.UUID, version_id: uuid.UUID, ocr: OcrClient | None, report: StepReporter | None = None
) -> str:
    """Process one document version end to end. Returns the document's final status."""
    async with tenant_session(firm_id) as db:
        version = await db.get(DocumentVersion, version_id)
        if version is None:
            return "failed"
        doc = await db.get(Document, version.document_id)
        if doc is None or doc.deleted_at is not None or doc.current_version_id != version.id:
            return "skipped"  # deleted, or superseded by a newer version while queued
        doc.status = "processing"
        storage_key, mime = version.storage_key, version.mime

    try:
        data = get_storage().read(storage_key)
        pages = extract(data, mime)
        pending = await _apply_ocr(pages, data, version, ocr)
        full_text = "\n\n".join(p.text for p in pages if p.text)
        doc_type = classify(full_text)
        drafts = chunk_pages([p for p in pages if p.text])
        status = "partial" if pending else "indexed"
        error = None if drafts else ("Awaiting OCR - no text layer" if pending else "No text found")
        if not drafts and not pending:
            status = "failed"
    except UnsupportedFile as exc:
        pages, pending, doc_type, drafts, status, error = [], [], "other", [], "failed", str(exc)
    except Exception as exc:
        log.exception("ingest failed for version %s", version_id)
        pages, pending, doc_type, drafts, status, error = (
            [],
            [],
            "other",
            [],
            "failed",
            f"{type(exc).__name__}: {exc}",
        )

    async with tenant_session(firm_id) as db:
        doc = await db.get(Document, version.document_id)
        if doc is None:
            return "failed"
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))  # replaces older version's text
        passages: list[hybrid.Passage] = []
        for d in drafts:
            chunk_id = uuid.uuid4()
            passages.append(hybrid.Passage(chunk_id, doc.matter_id, doc.id, version_id, doc_type, d.text))
            db.add(
                Chunk(
                    id=chunk_id,
                    firm_id=firm_id,
                    matter_id=doc.matter_id,
                    document_id=doc.id,
                    version_id=version_id,
                    ordinal=d.ordinal,
                    section_path=d.section_path,
                    page_from=d.page_from,
                    page_to=d.page_to,
                    char_start=d.char_start,
                    char_end=d.char_end,
                    text=d.text,
                    ocr_confidence=d.ocr_confidence,
                )
            )
        doc.doc_type, doc.status, doc.error = doc_type, status, error
        doc.page_count = len(pages) or None
        doc.ocr_pending_pages = pending
        document_id = doc.id

    await _embed(firm_id, document_id, passages, report)
    return status


async def _embed(
    firm_id: uuid.UUID, document_id: uuid.UUID, passages: list[hybrid.Passage], report: StepReporter | None
) -> None:
    """Vectors for the passages just saved. Failure leaves them pending; it never fails the document."""

    async def progress(done: int, total: int) -> None:
        if report is not None:
            await report(f"understanding meaning {done}/{total} passages")

    try:
        model = await hybrid.index_document(firm_id, document_id, passages, progress)
        if model and passages:
            await hybrid.mark_embedded(firm_id, [x.chunk_id for x in passages], model)
    except (EmbeddingError, VectorStoreError) as exc:
        log.warning("semantic index pending for document %s: %s", document_id, exc)
    except Exception:  # a bug here must not undo an ingestion whose text is already searchable
        log.exception("semantic index failed for document %s", document_id)


async def run_ingest_job(firm_id: uuid.UUID, job_id: uuid.UUID) -> None:
    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        version_ids = [uuid.UUID(v) for v in job.payload.get("version_ids", [])]
        start_at = job.done  # resume after a crash: files before `done` are finished
        rows = await db.execute(
            select(DocumentVersion.id, DocumentVersion.original_filename).where(
                DocumentVersion.id.in_(version_ids)
            )
        )
        names: dict[uuid.UUID, str] = {r[0]: r[1] for r in rows}

    ocr = get_ocr_client()
    failed: list[str] = []
    for i, vid in enumerate(version_ids):
        if i < start_at:
            continue
        name = names.get(vid, str(vid))
        prefix = f"{i + 1}/{len(version_ids)} {name}"
        await _set_step(firm_id, job_id, prefix)

        async def report(detail: str, prefix: str = prefix) -> None:
            await _set_step(firm_id, job_id, f"{prefix}: {detail}")

        status = await ingest_version(firm_id, vid, ocr, report)
        if status == "failed":
            failed.append(name)
        async with tenant_session(firm_id) as db:
            job = await db.get(Job, job_id)
            if job is not None:
                job.done = i + 1

    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is not None:
            job.status = "failed" if failed and len(failed) == len(version_ids) else "succeeded"
            job.error = f"{len(failed)} file(s) failed: {', '.join(failed)[:500]}" if failed else None
            job.current_step = f"finished {datetime.now(UTC):%H:%M:%S} UTC"
