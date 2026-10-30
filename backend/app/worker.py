"""Background worker (ADR-009). Run as its own process in production:  python -m app.worker

Claims one job at a time with claim_next_job() (FOR UPDATE SKIP LOCKED), so any number of workers can
run side by side. A job that crashes mid-way is reclaimed after 10 minutes and resumes from `done`.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine, tenant_session

log = logging.getLogger("dataviv.worker")

Handler = Callable[[uuid.UUID, uuid.UUID], Awaitable[None]]


def _handlers() -> dict[str, Handler]:
    from app.modules.agents.runtime import run_agent_job
    from app.modules.review.extract import run_review_job
    from app.modules.vault.pipeline import run_ingest_job
    from app.modules.workflows.engine import run_workflow_job

    # Order is priority: after every job the worker looks for uploads to ingest first.
    return {
        "ingest": run_ingest_job,
        "review_table": run_review_job,
        "workflow": run_workflow_job,
        "agent": run_agent_job,
    }


async def claim(kind: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    async with get_engine().begin() as conn:
        row = (
            await conn.execute(text("SELECT job_id, firm_id FROM claim_next_job(:k)"), {"k": kind})
        ).first()
    return (row[0], row[1]) if row else None


async def _mark_failed(firm_id: uuid.UUID, job_id: uuid.UUID, exc: BaseException) -> None:
    from app.core.models import Job

    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is not None:
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"[:1000]


async def run_pending(max_jobs: int = 100) -> int:
    """Process queued jobs until none are left (or max_jobs). Returns how many ran. Used by tests too.

    After each job the search restarts from the highest-priority kind, so a long review table
    (which re-queues itself every few documents) never holds up newly uploaded files.
    """
    ran = 0
    handlers = _handlers()
    while ran < max_jobs:
        for kind, handler in handlers.items():
            claimed = await claim(kind)
            if claimed is None:
                continue
            job_id, firm_id = claimed
            try:
                await handler(firm_id, job_id)
            except Exception as exc:
                log.exception("job %s failed", job_id)
                await _mark_failed(firm_id, job_id, exc)
            ran += 1
            break
        else:
            break  # nothing queued of any kind
    return ran


async def sweep_signatures() -> int:
    """Check envelopes still out for signature (Step 2e); a no-op when e-signature is off."""
    from app.modules.esign.service import sync_open

    return await sync_open()


async def worker_loop(stop: asyncio.Event | None = None) -> None:
    s = get_settings()
    poll = s.worker_poll_seconds
    log.info("worker started (poll %.1fs)", poll)
    loop = asyncio.get_running_loop()
    next_sweep = loop.time() + 5.0
    while stop is None or not stop.is_set():
        try:
            ran = await run_pending(max_jobs=10)
        except Exception:
            log.exception("worker poll failed")
            ran = 0
        if loop.time() >= next_sweep:
            next_sweep = loop.time() + max(10.0, s.esign_poll_seconds / 2)
            try:
                checked = await sweep_signatures()
                if checked:
                    log.info("checked %d signature request(s)", checked)
            except Exception:
                log.exception("signature sweep failed")
        if ran == 0:
            await asyncio.sleep(poll)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(worker_loop())
