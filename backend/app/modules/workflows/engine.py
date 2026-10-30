"""The durable workflow engine (WFL-03, WFL-05; ADR-017).

A run is a row in workflow_runs with one row per step in workflow_steps. The worker's "workflow"
job advances a run ONE step (or one slice of a long step) per invocation and then re-queues itself,
so uploads and review tables are never stuck behind a long workflow, and a crash loses at most the
step in flight:

    claim job → lock run → pick the next unfinished step → mark it running   (transaction 1)
    run the step's handler (model calls, no transaction held)
    lock run → store output, tokens and cost → step done → re-queue job      (transaction 2)

A step that crashed mid-way is still "running" when the job is reclaimed (after 10 minutes), and is
simply run again; after three attempts it fails. An approval step stops the run (waiting_approval)
and ends the job; the decision creates a new job. The run keeps a snapshot of the definition it
started with, so editing a definition never changes a run already under way.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.models import Firm, Job, User
from app.core.permissions import EDIT_ROLES, user_matter_role
from app.core.security import Principal, Role
from app.db.session import tenant_session
from app.modules.matters.models import Matter
from app.modules.workflows.models import WorkflowRun, WorkflowStep
from app.modules.workflows.spec import render
from app.modules.workflows.steps import HANDLERS, StepContext, StepFailed, StepResult, snapshot_playbook

log = logging.getLogger("dataviv.workflows")
FINISHED = frozenset({"succeeded", "failed", "rejected", "cancelled"})
MAX_ATTEMPTS = 3


def new_job(run: WorkflowRun, steps_done: int, total: int) -> Job:
    return Job(
        firm_id=run.firm_id,
        matter_id=run.matter_id,
        kind="workflow",
        status="queued",
        total=total,
        done=steps_done,
        created_by=run.created_by,
        current_step="Waiting to start" if steps_done == 0 else "Waiting to continue",
        payload={"run_id": str(run.id)},
    )


async def _steps(db: AsyncSession, run_id: uuid.UUID) -> list[WorkflowStep]:
    return list(
        (
            await db.scalars(
                select(WorkflowStep).where(WorkflowStep.run_id == run_id).order_by(WorkflowStep.position)
            )
        ).all()
    )


async def principal_for(db: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID) -> Principal:
    user = await db.get(User, user_id)
    return Principal(
        user_id=user_id,
        firm_id=firm_id,
        role=cast(Role, user.role if user else "associate"),
        email=user.email if user else "",
    )


def _now() -> datetime:
    return datetime.now(UTC)


def skip_remaining(steps: list[WorkflowStep]) -> None:
    for s in steps:
        if s.status in ("pending", "running", "waiting"):
            s.status = "skipped"


async def fail_run(
    db: AsyncSession, p: Principal, run: WorkflowRun, job: Job | None, step: WorkflowStep | None, error: str
) -> None:
    now = _now()
    if step is not None:
        step.status, step.error, step.finished_at = "failed", error[:2000], now
    run.status, run.error, run.finished_at, run.updated_at = (
        "failed",
        (f"{step.title}: {error}" if step else error)[:2000],
        now,
        now,
    )
    if job is not None:
        job.status, job.error = "failed", run.error
    await audit.record(
        db,
        p,
        "workflow.failed",
        "workflow_run",
        run.id,
        run.matter_id,
        {"step": step.step_key if step else None, "error": error[:500]},
    )


async def _context(db: AsyncSession, run: WorkflowRun, steps: list[WorkflowStep]) -> dict[str, Any]:
    matter = await db.get(Matter, run.matter_id)
    return {
        "inputs": run.inputs,
        "steps": {s.step_key: s.output for s in steps if s.status == "done"},
        "matter": {
            "title": matter.title if matter else "",
            "client_name": matter.client_name if matter else "",
            "forum": (matter.forum or "") if matter else "",
            "cnr": (matter.cnr or "") if matter else "",
        },
    }


async def _resolve(
    db: AsyncSession, run: WorkflowRun, step: WorkflowStep, ctx: dict[str, Any]
) -> dict[str, Any]:
    spec = next(s for s in run.spec["steps"] if s["id"] == step.step_key)
    resolved = render({k: v for k, v in spec.items() if k not in ("id", "type", "title")}, ctx)
    if step.type == "playbook_review":
        resolved["playbook_snapshot"] = await snapshot_playbook(db, resolved.get("playbook"))
    return cast(dict[str, Any], resolved)


async def _begin(firm_id: uuid.UUID, job_id: uuid.UUID) -> StepContext | None:
    """Transaction 1: pick the step to run and mark it running. None = nothing to do now."""
    async with tenant_session(firm_id) as db:
        job = await db.get(Job, job_id)
        if job is None or job.status == "cancelled":
            return None
        run = await db.get(WorkflowRun, uuid.UUID(job.payload["run_id"]), with_for_update=True)
        if run is None or run.status in FINISHED or run.status == "waiting_approval":
            job.status = "cancelled" if run is not None and run.status == "cancelled" else "succeeded"
            return None
        p = await principal_for(db, firm_id, run.created_by)
        steps = await _steps(db, run.id)
        job.total = len(steps)
        job.done = sum(1 for s in steps if s.status in ("done", "skipped"))
        if await user_matter_role(db, run.created_by, run.matter_id) not in EDIT_ROLES:
            await fail_run(
                db, p, run, job, None, "The person who started this run no longer works on the matter"
            )
            skip_remaining(steps)
            return None
        step = next((s for s in steps if s.status not in ("done", "skipped")), None)
        if step is None:
            now = _now()
            run.status, run.finished_at, run.updated_at = "succeeded", now, now
            job.status, job.current_step = "succeeded", f"Finished {now:%H:%M} UTC"
            await audit.record(
                db,
                p,
                "workflow.succeeded",
                "workflow_run",
                run.id,
                run.matter_id,
                {"cost_usd": float(run.cost_usd)},
            )
            return None
        if step.status == "waiting":
            run.status, job.status, job.current_step = "waiting_approval", "succeeded", "Waiting for approval"
            return None
        if step.status == "failed":
            await fail_run(db, p, run, job, step, step.error or "failed")
            return None
        if step.attempts >= MAX_ATTEMPTS:
            await fail_run(db, p, run, job, step, f"Stopped after {MAX_ATTEMPTS} attempts")
            skip_remaining(steps)
            return None
        ctx = await _context(db, run, steps)
        if step.status == "pending":
            try:
                step.resolved = await _resolve(db, run, step, ctx)
            except StepFailed as e:
                await fail_run(db, p, run, job, step, str(e))
                skip_remaining(steps)
                return None
            step.started_at = _now()
        step.status, step.attempts, step.error = "running", step.attempts + 1, None
        run.status, run.updated_at = "running", _now()
        job.current_step = f"Step {step.position + 1} of {len(steps)}: {step.title}"
        firm = await db.get(Firm, firm_id)
        return StepContext(
            firm_id=firm_id,
            run_id=run.id,
            run_name=run.name,
            matter_id=run.matter_id,
            principal=p,
            policy=firm.model_policy if firm else {},
            step_id=step.id,
            step_key=step.step_key,
            step_title=step.title,
            params=dict(step.resolved),
            previous=dict(step.output or {}),
            steps=ctx["steps"],
            step_types={s.step_key: s.type for s in steps},
        )


async def _finish(
    firm_id: uuid.UUID, job_id: uuid.UUID, sc: StepContext, result: StepResult | None, error: str | None
) -> None:
    """Transaction 2: record what the step produced and move the run on."""
    async with tenant_session(firm_id) as db:
        run = await db.get(WorkflowRun, sc.run_id, with_for_update=True)
        step = await db.get(WorkflowStep, sc.step_id)
        job = await db.get(Job, job_id)
        if run is None or step is None:
            return
        p = sc.principal
        if run.status == "cancelled":
            step.status = "skipped"
            return
        if result is not None:  # bill the model work even if a later part of the step failed
            for task, c in result.completions:
                step.model = c.model
                step.input_tokens += c.usage.input_tokens
                step.output_tokens += c.usage.output_tokens
                step.cost_usd += Decimal(str(round(c.cost_usd, 6)))
                await audit.record(
                    db,
                    p,
                    "ai.call",
                    "workflow_run",
                    run.id,
                    run.matter_id,
                    {
                        "task": task,
                        "step": step.step_key,
                        "model": c.model,
                        "input_tokens": c.usage.input_tokens,
                        "output_tokens": c.usage.output_tokens,
                        "cost_usd": c.cost_usd,
                    },
                )
            if result.tokens:
                step.input_tokens, step.output_tokens = result.tokens[0], result.tokens[1]
                step.cost_usd = Decimal(str(result.tokens[2]))
            run.input_tokens = sum(s.input_tokens for s in await _steps(db, run.id))
            run.output_tokens = sum(s.output_tokens for s in await _steps(db, run.id))
            run.cost_usd = sum((s.cost_usd for s in await _steps(db, run.id)), Decimal(0))
        if error is not None or result is None:
            await fail_run(db, p, run, job, step, error or "failed")
            skip_remaining(await _steps(db, run.id))
            return
        output = dict(result.output)
        if result.apply is not None:
            output.update(await result.apply(db))
        step.output = output
        now = _now()
        run.updated_at = now
        if result.status == "waiting":
            step.status = "waiting"
            run.status = "waiting_approval"
            if job is not None:
                job.status, job.current_step = "succeeded", "Waiting for approval"
            return
        if result.status == "done":
            step.status, step.finished_at = "done", now
            await audit.record(
                db,
                p,
                "workflow.step_done",
                "workflow_run",
                run.id,
                run.matter_id,
                {"step": step.step_key, "type": step.type},
            )
        else:  # "running": a slice of a long step finished; carry on next time
            step.attempts = 0
        if job is not None:
            job.status, job.attempts = "queued", 0
            job.done = sum(1 for s in await _steps(db, run.id) if s.status in ("done", "skipped"))


async def run_workflow_job(firm_id: uuid.UUID, job_id: uuid.UUID) -> None:
    sc = await _begin(firm_id, job_id)
    if sc is None:
        return
    result: StepResult | None = None
    error: str | None = None
    try:
        result = await HANDLERS[sc.step_types[sc.step_key]](sc)
    except StepFailed as e:
        error = str(e)
    except Exception as e:
        log.exception("workflow step %s failed", sc.step_key)
        error = f"Unexpected error ({type(e).__name__}): {e}"[:1000]
    await _finish(firm_id, job_id, sc, result, error)
