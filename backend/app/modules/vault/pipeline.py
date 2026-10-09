"""Ingestion pipeline (VLT-03): one job = one upload batch; progress is reported per file (NFR-09).

Each file is processed in its own firm-scoped transaction, so one bad file never blocks the rest and the
job row shows live progress. Stages: load → extract → OCR → classify → chunk → index.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core.models import Job
from app.core.storage import get_storage
from app.db.session import tenant_session
from app.modules.vault.chunker import chunk_pages
from app.modules.vault.classify import classify
from app.modules.vault.extract import Page, UnsupportedFile, extract
from app.modules.vault.models import Chunk, Document, DocumentVersion
from app.modules.vault.ocr import OcrClient, OcrUnavailable, get_ocr_client

log = logging.getLogger("dataviv.vault")


async def _set_step(firm_id: uuid.UUID, job_id: uuid.UUID, step: str) -> None:
    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is not None:
            job.current_step = step


async def _apply_ocr(pages: list[Page], data: bytes, version: DocumentVersion, ocr: OcrClient | None) -> list[int]:
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


async def ingest_version(firm_id: uuid.UUID, version_id: uuid.UUID, ocr: OcrClient | None) -> str:
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
    except Exception as exc:  # noqa: BLE001 - record any failure on the document, keep the batch going
        log.exception("ingest failed for version %s", version_id)
        pages, pending, doc_type, drafts, status, error = [], [], "other", [], "failed", f"{type(exc).__name__}: {exc}"

    async with tenant_session(firm_id) as db:
        doc = await db.get(Document, version.document_id)
        if doc is None:
            return "failed"
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))  # replaces older version's text
        for d in drafts:
            db.add(
                Chunk(
                    firm_id=firm_id, matter_id=doc.matter_id, document_id=doc.id, version_id=version_id,
                    ordinal=d.ordinal, section_path=d.section_path, page_from=d.page_from, page_to=d.page_to,
                    char_start=d.char_start, char_end=d.char_end, text=d.text, ocr_confidence=d.ocr_confidence,
                )
            )
        doc.doc_type, doc.status, doc.error = doc_type, status, error
        doc.page_count = len(pages) or None
        doc.ocr_pending_pages = pending
    return status


async def run_ingest_job(firm_id: uuid.UUID, job_id: uuid.UUID) -> None:
    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        version_ids = [uuid.UUID(v) for v in job.payload.get("version_ids", [])]
        start_at = job.done  # resume after a crash: files before `done` are finished
        names = dict(
            (await db.execute(
                select(DocumentVersion.id, DocumentVersion.original_filename).where(
                    DocumentVersion.id.in_(version_ids)
                )
            )).tuples().all()
        )

    ocr = get_ocr_client()
    failed: list[str] = []
    for i, vid in enumerate(version_ids):
        if i < start_at:
            continue
        name = names.get(vid, str(vid))
        await _set_step(firm_id, job_id, f"{i + 1}/{len(version_ids)} {name}")
        status = await ingest_version(firm_id, vid, ocr)
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
