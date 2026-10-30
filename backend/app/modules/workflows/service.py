"""Workflows, runs, approvals and playbooks (WFL-01..07).

Who may do what:

| Action                                   | who                                                    |
|------------------------------------------|--------------------------------------------------------|
| list and read workflows, playbooks       | anyone in the firm                                     |
| save a workflow definition or playbook   | partners and admins                                    |
| start, cancel, retry a run               | lead, partner or member on the matter                  |
| see a run                                | anyone on the matter (others: 404, ADR-007)            |
| approve or reject a step                 | matter_manager: lead/partner on the matter; firm_partner: a partner on the matter; never the person who started the run |
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.models import Job, User
from app.core.permissions import (
    EDIT_ROLES,
    MANAGER_ROLES,
    accessible_matter_ids,
    matter_role,
    require_matter_access,
)
from app.core.security import Principal
from app.modules.vault.models import Document
from app.modules.workflows.engine import FINISHED, new_job, skip_remaining
from app.modules.workflows.models import Approval, Playbook, WorkflowDef, WorkflowRun, WorkflowStep
from app.modules.workflows.schemas import (
    ApprovalOut,
    Decision,
    JobOut,
    Person,
    PlaybookCreate,
    PlaybookIn,
    PlaybookOut,
    RunCreate,
    RunDetail,
    RunSummary,
    SpecCheck,
    StepOut,
    WorkflowSummary,
)
from app.modules.workflows.spec import SpecError, validate
from app.modules.workflows.templates import PLAYBOOK_TEMPLATES, builtins

AUTHORS = frozenset({"partner", "admin"})


def _now() -> datetime:
    return datetime.now(UTC)


async def _names(db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return dict((await db.execute(select(User.id, User.name).where(User.id.in_(real)))).tuples().all())


def _person(names: dict[uuid.UUID, str], uid: uuid.UUID) -> Person:
    return Person(id=uid, name=names.get(uid, "Unknown"))


# --------------------------------------------------------------------------- definitions


def _summary(spec: dict[str, Any], version: int, builtin: bool) -> WorkflowSummary:
    return WorkflowSummary(
        slug=spec["slug"],
        version=version,
        name=spec["name"],
        description=spec.get("description", ""),
        builtin=builtin,
        inputs=spec.get("inputs", []),
        steps=[{"id": s["id"], "type": s["type"], "title": s.get("title") or s["id"]} for s in spec["steps"]],
    )


async def _firm_defs(db: AsyncSession) -> dict[str, WorkflowDef]:
    """The latest version of each of the firm's own definitions."""
    rows = (await db.scalars(select(WorkflowDef).order_by(WorkflowDef.slug, WorkflowDef.version))).all()
    return {d.slug: d for d in rows}


async def list_workflows(db: AsyncSession, p: Principal) -> list[WorkflowSummary]:
    out = [_summary(spec, int(spec.get("version", 1)), True) for spec in builtins().values()]
    out += [_summary(d.spec, d.version, False) for d in (await _firm_defs(db)).values()]
    return out


async def resolve_spec(db: AsyncSession, slug: str, version: int | None) -> tuple[dict[str, Any], int]:
    if slug in builtins():
        spec = builtins()[slug]
        return spec, int(spec.get("version", 1))
    stmt = select(WorkflowDef).where(WorkflowDef.slug == slug)
    stmt = (
        stmt.where(WorkflowDef.version == version) if version else stmt.order_by(WorkflowDef.version.desc())
    )
    d = (await db.scalars(stmt.limit(1))).first()
    if d is None:
        raise NotFound("Workflow not found")
    return d.spec, d.version


async def get_workflow(db: AsyncSession, p: Principal, slug: str) -> dict[str, Any]:
    spec, version = await resolve_spec(db, slug, None)
    return {**spec, "version": version, "builtin": slug in builtins()}


def check_spec(spec: dict[str, Any]) -> SpecCheck:
    try:
        validate(spec)
    except SpecError as e:
        return SpecCheck(valid=False, problems=e.problems)
    return SpecCheck(valid=True, problems=[])


async def save_workflow(db: AsyncSession, p: Principal, spec: dict[str, Any]) -> WorkflowSummary:
    if p.role not in AUTHORS:
        raise Forbidden("Only partners and admins can publish workflows")
    check = check_spec(spec)
    if not check.valid:
        raise Unprocessable("The definition has problems: " + "; ".join(check.problems))
    slug = spec["slug"]
    if slug in builtins():
        raise Conflict(f"“{slug}” is a built-in workflow. Choose another slug for your version.")
    latest = await db.scalar(select(func.max(WorkflowDef.version)).where(WorkflowDef.slug == slug))
    d = WorkflowDef(
        firm_id=p.firm_id,
        slug=slug,
        version=(latest or 0) + 1,
        name=spec["name"],
        spec=spec,
        created_by=p.user_id,
    )
    db.add(d)
    await db.flush()
    await audit.record(
        db, p, "workflow.publish", "workflow_def", d.id, None, {"slug": slug, "version": d.version}
    )
    return _summary(spec, d.version, False)


# --------------------------------------------------------------------------- starting a run


async def _check_inputs(
    db: AsyncSession, spec: dict[str, Any], matter_id: uuid.UUID, given: dict[str, Any]
) -> dict[str, Any]:
    problems: list[str] = []
    known = {i["key"] for i in spec.get("inputs", [])}
    for k in given:
        if k not in known:
            problems.append(f"{k}: this workflow has no such input")
    out: dict[str, Any] = {}
    for inp in spec.get("inputs", []):
        key, kind, label = inp["key"], inp["type"], inp.get("label", inp["key"])
        v = given.get(key, inp.get("default"))
        empty = v is None or v == "" or v == []
        if empty:
            if inp.get("required"):
                problems.append(f"{label}: required")
            out[key] = [] if kind == "documents" else ""
            continue
        try:
            if kind in ("text", "textarea"):
                v = str(v).strip()
                if len(v) > (8000 if kind == "textarea" else 500):
                    problems.append(f"{label}: too long")
            elif kind == "number":
                v = float(v)
                v = int(v) if v.is_integer() else v
                if ("min" in inp and v < inp["min"]) or ("max" in inp and v > inp["max"]):
                    problems.append(f"{label}: must be between {inp.get('min')} and {inp.get('max')}")
            elif kind == "choice":
                if v not in inp["choices"]:
                    problems.append(f"{label}: choose one of {', '.join(inp['choices'])}")
            elif kind in ("document", "documents"):
                ids = [uuid.UUID(str(x)) for x in (v if isinstance(v, list) else [v])]
                found = set(
                    (
                        await db.scalars(
                            select(Document.id).where(
                                Document.id.in_(ids),
                                Document.matter_id == matter_id,
                                Document.deleted_at.is_(None),
                            )
                        )
                    ).all()
                )
                if len(found) != len(set(ids)):
                    problems.append(f"{label}: not a document in this matter")
                if kind == "document" and len(ids) != 1:
                    problems.append(f"{label}: choose one document")
                v = [str(i) for i in ids] if kind == "documents" else str(ids[0])
            elif kind == "playbook":
                if await db.get(Playbook, uuid.UUID(str(v))) is None:
                    problems.append(f"{label}: playbook not found")
                v = str(v)
        except (ValueError, TypeError):
            problems.append(f"{label}: not a valid {kind}")
        out[key] = v
    if problems:
        raise Unprocessable("; ".join(problems))
    return out


async def start_run(db: AsyncSession, p: Principal, matter_id: uuid.UUID, body: RunCreate) -> RunDetail:
    role = await require_matter_access(db, p, matter_id)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot start workflows")
    spec, version = await resolve_spec(db, body.workflow, body.version)
    validate(spec)
    inputs = await _check_inputs(db, spec, matter_id, body.inputs)
    run = WorkflowRun(
        firm_id=p.firm_id,
        matter_id=matter_id,
        slug=spec["slug"],
        version=version,
        name=spec["name"],
        spec=spec,
        inputs=inputs,
        status="queued",
        created_by=p.user_id,
    )
    db.add(run)
    await db.flush()
    for i, st in enumerate(spec["steps"]):
        db.add(
            WorkflowStep(
                firm_id=p.firm_id,
                matter_id=matter_id,
                run_id=run.id,
                position=i,
                step_key=st["id"],
                type=st["type"],
                title=st.get("title") or st["id"],
                status="pending",
            )
        )
    job = new_job(run, 0, len(spec["steps"]))
    db.add(job)
    await db.flush()
    run.job_id = job.id
    await db.flush()
    await db.refresh(run)  # load the server-side timestamps before the response reads them
    await audit.record(
        db,
        p,
        "workflow.start",
        "workflow_run",
        run.id,
        matter_id,
        {"workflow": run.slug, "version": version, "inputs": list(inputs)},
    )
    return await run_detail(db, p, run.id)


# --------------------------------------------------------------------------- reading runs


async def _run(
    db: AsyncSession, p: Principal, run_id: uuid.UUID, *, lock: bool = False
) -> tuple[WorkflowRun, str]:
    run = await db.get(WorkflowRun, run_id, with_for_update=lock)
    if run is None:
        raise NotFound("Workflow run not found")
    try:
        role = await require_matter_access(db, p, run.matter_id)
    except NotFound:
        raise NotFound("Workflow run not found") from None
    return run, role


async def _steps(db: AsyncSession, run_id: uuid.UUID) -> list[WorkflowStep]:
    return list(
        (
            await db.scalars(
                select(WorkflowStep).where(WorkflowStep.run_id == run_id).order_by(WorkflowStep.position)
            )
        ).all()
    )


def can_decide(p: Principal, approval: Approval, run: WorkflowRun, role: str | None) -> bool:
    if approval.status != "pending" or p.user_id == run.created_by or role is None:
        return False
    if approval.required_role == "firm_partner":
        return p.role == "partner"
    return role in MANAGER_ROLES


def _approval_out(a: Approval, names: dict[uuid.UUID, str], can: bool, run_name: str | None) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        run_id=a.run_id or a.agent_run_id,
        matter_id=a.matter_id,
        step_id=a.step_id or a.agent_step_id,
        required_role=a.required_role,
        message=a.message,
        status=a.status,
        requested_by=_person(names, a.requested_by),
        decided_by=_person(names, a.decided_by) if a.decided_by else None,
        decided_at=a.decided_at,
        comment=a.comment,
        created_at=a.created_at,
        can_decide=can,
        run_name=run_name,
        subject="agent" if a.agent_run_id else "workflow",
        detail=a.detail or {},
    )


def _summary_out(run: WorkflowRun, names: dict[uuid.UUID, str], done: int, total: int) -> dict[str, Any]:
    return {
        "id": run.id,
        "matter_id": run.matter_id,
        "slug": run.slug,
        "version": run.version,
        "name": run.name,
        "status": run.status,
        "created_by": _person(names, run.created_by),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "cost_usd": float(run.cost_usd),
        "error": run.error,
        "steps_done": done,
        "steps_total": total,
    }


async def run_detail(db: AsyncSession, p: Principal, run_id: uuid.UUID) -> RunDetail:
    run, role = await _run(db, p, run_id)
    steps = await _steps(db, run.id)
    approvals = list(
        (
            await db.scalars(select(Approval).where(Approval.run_id == run.id).order_by(Approval.created_at))
        ).all()
    )
    job = await db.get(Job, run.job_id) if run.job_id else None
    names = await _names(
        db, {run.created_by} | {a.requested_by for a in approvals} | {a.decided_by for a in approvals}
    )
    done = sum(1 for s in steps if s.status == "done")
    return RunDetail(
        **_summary_out(run, names, done, len(steps)),
        inputs=run.inputs,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        steps=[
            StepOut(
                id=s.id,
                position=s.position,
                key=s.step_key,
                type=s.type,
                title=s.title,
                status=s.status,
                resolved={k: v for k, v in s.resolved.items() if k != "playbook_snapshot"},
                output=s.output,
                model=s.model,
                input_tokens=s.input_tokens,
                output_tokens=s.output_tokens,
                cost_usd=float(s.cost_usd),
                error=s.error,
                attempts=s.attempts,
                started_at=s.started_at,
                finished_at=s.finished_at,
            )
            for s in steps
        ],
        approvals=[_approval_out(a, names, can_decide(p, a, run, role), run.name) for a in approvals],
        job=JobOut(
            id=job.id,
            status=job.status,
            done=job.done,
            total=job.total,
            progress=job.progress,
            current_step=job.current_step,
        )
        if job
        else None,
        can_cancel=role in EDIT_ROLES and run.status not in FINISHED,
        can_retry=role in EDIT_ROLES and run.status == "failed",
    )


async def list_runs(db: AsyncSession, p: Principal, matter_id: uuid.UUID) -> list[RunSummary]:
    await require_matter_access(db, p, matter_id)
    runs = list(
        (
            await db.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.matter_id == matter_id)
                .order_by(WorkflowRun.created_at.desc())
            )
        ).all()
    )
    if not runs:
        return []
    counts: dict[uuid.UUID, tuple[int, int]] = {
        r[0]: (int(r[1]), int(r[2]))
        for r in (
            await db.execute(
                select(WorkflowStep.run_id, func.count().filter(WorkflowStep.status == "done"), func.count())
                .where(WorkflowStep.run_id.in_([r.id for r in runs]))
                .group_by(WorkflowStep.run_id)
            )
        ).all()
    }
    names = await _names(db, {r.created_by for r in runs})
    return [RunSummary(**_summary_out(r, names, *counts.get(r.id, (0, 0)))) for r in runs]


# --------------------------------------------------------------------------- cancel, retry


async def cancel(db: AsyncSession, p: Principal, run_id: uuid.UUID) -> RunDetail:
    run, role = await _run(db, p, run_id, lock=True)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot cancel workflows")
    if run.status in FINISHED:
        raise Conflict(f"This run has already {run.status.replace('_', ' ')}")
    now = _now()
    run.status, run.finished_at, run.updated_at = "cancelled", now, now
    skip_remaining(await _steps(db, run.id))
    for a in (
        await db.scalars(select(Approval).where(Approval.run_id == run.id, Approval.status == "pending"))
    ).all():
        a.status = "cancelled"
    job = await db.get(Job, run.job_id) if run.job_id else None
    if job is not None and job.status in ("queued", "running"):
        job.status = "cancelled"
    await audit.record(db, p, "workflow.cancel", "workflow_run", run.id, run.matter_id, {})
    return await run_detail(db, p, run.id)


async def retry(db: AsyncSession, p: Principal, run_id: uuid.UUID) -> RunDetail:
    """Re-run from the step that failed; finished steps keep their results."""
    run, role = await _run(db, p, run_id, lock=True)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot retry workflows")
    if run.status != "failed":
        raise Conflict("Only a failed run can be retried")
    steps = await _steps(db, run.id)
    for s in steps:
        if s.status in ("failed", "skipped"):
            # a long step that got part-way (a review table) resumes; anything else starts again
            s.status = "running" if s.type == "review_table" and s.output.get("table_id") else "pending"
            s.error, s.attempts, s.finished_at = None, 0, None
            if s.status == "pending":
                s.output = {}
    run.status, run.error, run.finished_at, run.updated_at = "queued", None, None, _now()
    job = new_job(run, sum(1 for s in steps if s.status == "done"), len(steps))
    db.add(job)
    await db.flush()
    run.job_id = job.id
    await audit.record(db, p, "workflow.retry", "workflow_run", run.id, run.matter_id, {})
    return await run_detail(db, p, run.id)


# --------------------------------------------------------------------------- approvals


async def list_approvals(db: AsyncSession, p: Principal, mine: bool) -> list[ApprovalOut]:
    """Pending approvals (workflow steps and agent actions) in matters you can see.

    mine=True: only those you can decide."""
    from app.modules.agents.service import list_agent_approvals  # agents build on workflows, not the reverse

    matters = await accessible_matter_ids(db, p)
    if not matters:
        return []
    rows = (
        (
            await db.execute(
                select(Approval, WorkflowRun)
                .join(WorkflowRun, WorkflowRun.id == Approval.run_id)
                .where(
                    Approval.status == "pending",
                    Approval.matter_id.in_(matters),
                    Approval.run_id.is_not(None),
                )
                .order_by(Approval.created_at)
            )
        )
        .tuples()
        .all()
    )
    roles: dict[uuid.UUID, str | None] = {}
    for _, run in rows:
        if run.matter_id not in roles:
            roles[run.matter_id] = await matter_role(db, p, run.matter_id)
    names = await _names(db, {a.requested_by for a, _ in rows})
    out = [_approval_out(a, names, can_decide(p, a, r, roles[r.matter_id]), r.name) for a, r in rows]
    out += await list_agent_approvals(db, p, matters)
    out.sort(key=lambda o: o.created_at)
    return [o for o in out if o.can_decide] if mine else out


async def decide(db: AsyncSession, p: Principal, approval_id: uuid.UUID, body: Decision) -> Any:
    """Returns the workflow RunDetail, or the agent RunDetail for an agent action."""
    approval = await db.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise NotFound("Approval not found")
    if approval.agent_run_id is not None:
        from app.modules.agents.service import decide_agent_action

        return await decide_agent_action(db, p, approval, body)
    assert approval.run_id is not None
    try:
        run, role = await _run(db, p, approval.run_id, lock=True)
    except NotFound:
        raise NotFound("Approval not found") from None
    if approval.status != "pending":
        raise Conflict(f"This approval has already been {approval.status}")
    if p.user_id == run.created_by:
        raise Forbidden("You started this run, so someone else must approve it")
    if not can_decide(p, approval, run, role):
        who = (
            "a partner"
            if approval.required_role == "firm_partner"
            else "the matter lead or a partner on the matter"
        )
        raise Forbidden(f"Only {who} can decide this")
    comment = (body.comment or "").strip() or None
    if body.decision == "reject" and not comment:
        raise Unprocessable("Say why you are rejecting it, so the team knows what to change")
    now = _now()
    approval.status = "approved" if body.decision == "approve" else "rejected"
    approval.decided_by, approval.decided_at, approval.comment = p.user_id, now, comment
    assert approval.step_id is not None
    step = await db.get(WorkflowStep, approval.step_id)
    steps = await _steps(db, run.id)
    if step is None:  # pragma: no cover - foreign key
        raise NotFound("Approval not found")
    names = await _names(db, {p.user_id})
    step.output = {
        **step.output,
        "status": approval.status,
        "decided_by_id": str(p.user_id),
        "decided_by": names.get(p.user_id, ""),
        "comment": comment,
        "decided_at": now.isoformat(),
    }
    step.finished_at = now
    if body.decision == "approve":
        step.status = "done"
        run.status, run.updated_at = "queued", now
        job = new_job(run, sum(1 for s in steps if s.status == "done"), len(steps))
        db.add(job)
        await db.flush()
        run.job_id = job.id
    else:
        step.status, step.error = "failed", f"Rejected by {names.get(p.user_id, 'the approver')}: {comment}"
        run.status, run.error, run.finished_at, run.updated_at = "rejected", step.error, now, now
        skip_remaining(steps)
    await audit.record(
        db,
        p,
        f"workflow.{approval.status}",
        "workflow_run",
        run.id,
        run.matter_id,
        {"approval_id": str(approval.id), "step": step.step_key, "comment": comment},
    )
    return await run_detail(db, p, run.id)


# --------------------------------------------------------------------------- playbooks


def _playbook_out(b: Playbook, names: dict[uuid.UUID, str]) -> PlaybookOut:
    return PlaybookOut(
        id=b.id,
        name=b.name,
        contract_type=b.contract_type,
        description=b.description,
        clauses=b.clauses,
        version=b.version,
        updated_by=_person(names, b.updated_by),
        updated_at=b.updated_at,
    )


def playbook_templates() -> list[dict[str, Any]]:
    return PLAYBOOK_TEMPLATES


async def list_playbooks(db: AsyncSession, p: Principal) -> list[PlaybookOut]:
    books = list((await db.scalars(select(Playbook).order_by(Playbook.name))).all())
    names = await _names(db, {b.updated_by for b in books})
    return [_playbook_out(b, names) for b in books]


async def _playbook(db: AsyncSession, playbook_id: uuid.UUID) -> Playbook:
    b = await db.get(Playbook, playbook_id)
    if b is None:
        raise NotFound("Playbook not found")
    return b


async def get_playbook(db: AsyncSession, p: Principal, playbook_id: uuid.UUID) -> PlaybookOut:
    b = await _playbook(db, playbook_id)
    return _playbook_out(b, await _names(db, {b.updated_by}))


def _author(p: Principal) -> None:
    if p.role not in AUTHORS:
        raise Forbidden("Only partners and admins can change the firm's playbooks")


def _unique_keys(body: PlaybookIn) -> None:
    keys = [c.key for c in body.clauses]
    if len(keys) != len(set(keys)):
        raise Unprocessable("Each clause needs its own key")


async def create_playbook(db: AsyncSession, p: Principal, body: PlaybookCreate) -> PlaybookOut:
    _author(p)
    if body.from_template:
        t = next((t for t in PLAYBOOK_TEMPLATES if t["id"] == body.from_template), None)
        if t is None:
            raise NotFound("Playbook template not found")
        data = PlaybookIn(
            name=t["name"],
            contract_type=t["contract_type"],
            description=t["description"],
            clauses=t["clauses"],
        )
        if body.playbook:
            data = body.playbook
    elif body.playbook:
        data = body.playbook
    else:
        raise Unprocessable("Give a playbook or a template to start from")
    _unique_keys(data)
    b = Playbook(
        firm_id=p.firm_id,
        name=data.name,
        contract_type=data.contract_type,
        description=data.description,
        clauses=[c.model_dump() for c in data.clauses],
        version=1,
        created_by=p.user_id,
        updated_by=p.user_id,
    )
    db.add(b)
    await db.flush()
    await db.refresh(b)
    await audit.record(
        db, p, "playbook.create", "playbook", b.id, None, {"name": b.name, "clauses": len(b.clauses)}
    )
    return _playbook_out(b, await _names(db, {p.user_id}))


async def update_playbook(
    db: AsyncSession, p: Principal, playbook_id: uuid.UUID, body: PlaybookIn
) -> PlaybookOut:
    _author(p)
    _unique_keys(body)
    b = await _playbook(db, playbook_id)
    b.name, b.contract_type, b.description = body.name, body.contract_type, body.description
    b.clauses = [c.model_dump() for c in body.clauses]
    b.version, b.updated_by, b.updated_at = b.version + 1, p.user_id, _now()
    await db.flush()
    await audit.record(db, p, "playbook.update", "playbook", b.id, None, {"version": b.version})
    return _playbook_out(b, await _names(db, {p.user_id}))


async def delete_playbook(db: AsyncSession, p: Principal, playbook_id: uuid.UUID) -> None:
    _author(p)
    b = await _playbook(db, playbook_id)
    await db.delete(b)
    await audit.record(db, p, "playbook.delete", "playbook", playbook_id, None, {"name": b.name})
