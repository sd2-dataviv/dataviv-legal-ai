"""Agents, agent runs, agent approvals and matter memory (AGT-01..07).

| Action                                         | who                                                    |
|------------------------------------------------|--------------------------------------------------------|
| list and read agents                           | anyone in the firm                                     |
| publish an agent definition                    | partners and admins                                    |
| start or cancel a run; add or remove memory    | lead, partner or member on the matter                  |
| see a run, its trace and the matter memory     | anyone on the matter (others: 404, ADR-007)            |
| confirm an action ("confirm" policy)           | lead, partner or member on the matter, including the person who started it |
| approve an action ("manager" policy)           | the matter lead or a partner on the matter, not the person who started it  |
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.models import Job, User
from app.core.permissions import EDIT_ROLES, MANAGER_ROLES, matter_role, require_matter_access
from app.core.security import Principal
from app.modules.vault.models import Document
from app.modules.workflows.models import Approval
from app.modules.workflows.schemas import ApprovalOut, Decision, JobOut, Person

from .models import AgentDef, AgentRun, AgentStep, MemoryItem
from .runtime import FINISHED, children_of, new_job
from .schemas import (
    AgentSummary,
    ChildOut,
    MemoryIn,
    MemoryOut,
    RunCreate,
    RunDetail,
    RunSummary,
    SpecCheck,
    StepOut,
)
from .spec import SpecError, budget, builtins, policy, validate

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


def _tools(spec: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": t, "policy": policy(spec, t)} for t in spec["tools"]]


# --------------------------------------------------------------------------- definitions


def _summary(spec: dict[str, Any], version: int, builtin: bool) -> AgentSummary:
    steps, cost = budget(spec)
    return AgentSummary(
        slug=spec["slug"],
        version=version,
        name=spec["name"],
        description=spec.get("description", ""),
        builtin=builtin,
        goal_hint=spec.get("goal_hint", ""),
        tools=_tools(spec),
        max_steps=steps,
        max_cost_usd=cost,
    )


async def _firm_defs(db: AsyncSession) -> dict[str, AgentDef]:
    rows = (await db.scalars(select(AgentDef).order_by(AgentDef.slug, AgentDef.version))).all()
    return {d.slug: d for d in rows}


async def _known(db: AsyncSession) -> dict[str, dict[str, Any]]:
    return {**{d.slug: d.spec for d in (await _firm_defs(db)).values()}, **builtins()}


async def list_agents(db: AsyncSession, p: Principal) -> list[AgentSummary]:
    out = [
        _summary(s, int(s.get("version", 1)), True) for s in builtins().values() if not s.get("subagent_only")
    ]
    out += [
        _summary(d.spec, d.version, False)
        for d in (await _firm_defs(db)).values()
        if not d.spec.get("subagent_only")
    ]
    return out


async def resolve_spec(db: AsyncSession, slug: str) -> tuple[dict[str, Any], int]:
    if slug in builtins():
        spec = builtins()[slug]
        return spec, int(spec.get("version", 1))
    d = (await _firm_defs(db)).get(slug)
    if d is None:
        raise NotFound("Agent not found")
    return d.spec, d.version


async def get_agent(db: AsyncSession, p: Principal, slug: str) -> dict[str, Any]:
    spec, version = await resolve_spec(db, slug)
    return {**spec, "version": version, "builtin": slug in builtins()}


async def check_spec(db: AsyncSession, spec: dict[str, Any]) -> SpecCheck:
    try:
        validate(spec, await _known(db))
    except SpecError as e:
        return SpecCheck(valid=False, problems=e.problems)
    return SpecCheck(valid=True, problems=[])


async def save_agent(db: AsyncSession, p: Principal, spec: dict[str, Any]) -> AgentSummary:
    if p.role not in AUTHORS:
        raise Forbidden("Only partners and admins can publish agents")
    check = await check_spec(db, spec)
    if not check.valid:
        raise Unprocessable("The definition has problems: " + "; ".join(check.problems))
    if spec["slug"] in builtins():
        raise Conflict(f"“{spec['slug']}” is a built-in agent. Choose another slug for your version.")
    latest = await db.scalar(select(func.max(AgentDef.version)).where(AgentDef.slug == spec["slug"]))
    d = AgentDef(
        firm_id=p.firm_id,
        slug=spec["slug"],
        version=(latest or 0) + 1,
        name=spec["name"],
        spec=spec,
        created_by=p.user_id,
    )
    db.add(d)
    await db.flush()
    await audit.record(
        db, p, "agent.publish", "agent_def", d.id, None, {"slug": d.slug, "version": d.version}
    )
    return _summary(spec, d.version, False)


# --------------------------------------------------------------------------- runs


async def start_run(db: AsyncSession, p: Principal, matter_id: uuid.UUID, body: RunCreate) -> RunDetail:
    role = await require_matter_access(db, p, matter_id)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot start agents")
    spec, version = await resolve_spec(db, body.agent)
    if spec.get("subagent_only"):
        raise Unprocessable("That agent only runs as a subagent")
    known = await _known(db)
    validate(spec, known)
    spec = dict(spec)
    if spec.get("subagent") and spec["subagent"] not in builtins():
        spec["subagent_spec"] = known[spec["subagent"]]  # snapshot, like the rest of the definition
    steps, cost = budget(spec)
    if body.max_steps:
        steps = min(steps, body.max_steps)
    if body.max_cost_usd:
        cost = min(cost, body.max_cost_usd)
    scope = None
    if body.document_ids:
        ids = list(dict.fromkeys(body.document_ids))
        found = (
            await db.scalars(
                select(Document.id).where(
                    Document.id.in_(ids), Document.matter_id == matter_id, Document.deleted_at.is_(None)
                )
            )
        ).all()
        if len(found) != len(ids):
            raise Unprocessable("One or more documents are not in this matter")
        scope = ids
    run = AgentRun(
        firm_id=p.firm_id,
        matter_id=matter_id,
        slug=spec["slug"],
        version=version,
        name=spec["name"],
        spec=spec,
        goal=body.goal.strip(),
        scope_document_ids=scope,
        status="queued",
        max_steps=steps,
        max_cost_usd=Decimal(str(cost)),
        created_by=p.user_id,
        sources=[],
        sentences=[],
        verification={},
    )
    db.add(run)
    await db.flush()
    job = new_job(run)
    db.add(job)
    await db.flush()
    run.job_id = job.id
    await db.flush()
    await db.refresh(run)
    await audit.record(
        db,
        p,
        "agent.start",
        "agent_run",
        run.id,
        matter_id,
        {
            "agent": run.slug,
            "version": version,
            "goal": run.goal[:500],
            "max_steps": steps,
            "max_cost_usd": cost,
            "documents": len(scope) if scope else None,
        },
    )
    return await run_detail(db, p, run.id)


async def _run(
    db: AsyncSession, p: Principal, run_id: uuid.UUID, *, lock: bool = False
) -> tuple[AgentRun, str]:
    run = await db.get(AgentRun, run_id, with_for_update=lock)
    if run is None:
        raise NotFound("Agent run not found")
    try:
        role = await require_matter_access(db, p, run.matter_id)
    except NotFound:
        raise NotFound("Agent run not found") from None
    return run, role


def _summary_out(r: AgentRun, names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": r.id,
        "matter_id": r.matter_id,
        "slug": r.slug,
        "version": r.version,
        "name": r.name,
        "goal": r.goal,
        "status": r.status,
        "created_by": _person(names, r.created_by),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "finished_at": r.finished_at,
        "steps_used": r.steps_used,
        "max_steps": r.max_steps,
        "cost_usd": float(r.cost_usd),
        "max_cost_usd": float(r.max_cost_usd),
        "error": r.error,
    }


def can_decide_agent(p: Principal, a: Approval, run: AgentRun, role: str | None) -> bool:
    if a.status != "pending" or role is None:
        return False
    if a.required_role == "matter_editor":
        return role in EDIT_ROLES
    return role in MANAGER_ROLES and p.user_id != run.created_by


def _approval_out(a: Approval, names: dict[uuid.UUID, str], can: bool, run: AgentRun) -> ApprovalOut:
    assert a.agent_run_id is not None and a.agent_step_id is not None
    return ApprovalOut(
        id=a.id,
        run_id=a.agent_run_id,
        matter_id=a.matter_id,
        step_id=a.agent_step_id,
        required_role=a.required_role,
        message=a.message,
        status=a.status,
        requested_by=_person(names, a.requested_by),
        decided_by=_person(names, a.decided_by) if a.decided_by else None,
        decided_at=a.decided_at,
        comment=a.comment,
        created_at=a.created_at,
        can_decide=can,
        run_name=run.name,
        subject="agent",
        detail=a.detail or {},
    )


async def run_detail(db: AsyncSession, p: Principal, run_id: uuid.UUID) -> RunDetail:
    run, role = await _run(db, p, run_id)
    steps = list(
        (
            await db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.position))
        ).all()
    )
    approvals = list(
        (
            await db.scalars(
                select(Approval).where(Approval.agent_run_id == run.id).order_by(Approval.created_at)
            )
        ).all()
    )
    children = await children_of(db, run.id)
    job = await db.get(Job, run.job_id) if run.job_id else None
    names = await _names(
        db, [run.created_by, *(a.requested_by for a in approvals), *(a.decided_by for a in approvals)]
    )
    return RunDetail(
        **_summary_out(run, names),
        parent_run_id=run.parent_run_id,
        scope_document_ids=run.scope_document_ids,
        tools=_tools(run.spec),
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        answer=run.answer,
        sentences=run.sentences,
        sources=[
            {k: v for k, v in s.items() if k != "text"} | {"excerpt": " ".join(str(s["text"]).split())[:280]}
            for s in run.sources
        ],
        verification=run.verification,
        steps=[
            StepOut(
                id=s.id,
                position=s.position,
                thought=s.thought,
                tool=s.tool,
                arguments=s.arguments,
                status=s.status,
                observation=s.observation,
                result=s.result,
                model=s.model,
                input_tokens=s.input_tokens,
                output_tokens=s.output_tokens,
                cost_usd=float(s.cost_usd),
                error=s.error,
                created_at=s.created_at,
                finished_at=s.finished_at,
            )
            for s in steps
        ],
        approvals=[_approval_out(a, names, can_decide_agent(p, a, run, role), run) for a in approvals],
        children=[
            ChildOut(
                id=c.id,
                name=c.name,
                goal=c.goal,
                status=c.status,
                steps_used=c.steps_used,
                cost_usd=float(c.cost_usd),
                answer=c.answer,
            )
            for c in children
        ],
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
        can_cancel=role in EDIT_ROLES and run.status not in FINISHED and run.parent_run_id is None,
    )


async def list_runs(db: AsyncSession, p: Principal, matter_id: uuid.UUID) -> list[RunSummary]:
    await require_matter_access(db, p, matter_id)
    runs = list(
        (
            await db.scalars(
                select(AgentRun)
                .where(AgentRun.matter_id == matter_id, AgentRun.parent_run_id.is_(None))
                .order_by(AgentRun.created_at.desc())
            )
        ).all()
    )
    names = await _names(db, [r.created_by for r in runs])
    return [RunSummary(**_summary_out(r, names)) for r in runs]


async def _cancel_tree(db: AsyncSession, run: AgentRun) -> None:
    now = _now()
    run.status, run.finished_at, run.updated_at = "cancelled", now, now
    for a in (
        await db.scalars(
            select(Approval).where(Approval.agent_run_id == run.id, Approval.status == "pending")
        )
    ).all():
        a.status = "cancelled"
    job = await db.get(Job, run.job_id) if run.job_id else None
    if job is not None and job.status in ("queued", "running"):
        job.status = "cancelled"
    for child in await children_of(db, run.id):
        if child.status not in FINISHED:
            await _cancel_tree(db, child)


async def cancel(db: AsyncSession, p: Principal, run_id: uuid.UUID) -> RunDetail:
    run, role = await _run(db, p, run_id, lock=True)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot cancel agents")
    if run.parent_run_id is not None:
        raise Conflict("This is a subagent: cancel the agent that started it")
    if run.status in FINISHED:
        raise Conflict(f"This run has already {run.status}")
    await _cancel_tree(db, run)
    await audit.record(db, p, "agent.cancel", "agent_run", run.id, run.matter_id, {})
    return await run_detail(db, p, run.id)


# --------------------------------------------------------------------------- approvals


async def list_agent_approvals(db: AsyncSession, p: Principal, matters: set[uuid.UUID]) -> list[ApprovalOut]:
    rows = (
        (
            await db.execute(
                select(Approval, AgentRun)
                .join(AgentRun, AgentRun.id == Approval.agent_run_id)
                .where(Approval.status == "pending", Approval.matter_id.in_(matters))
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
    names = await _names(db, [a.requested_by for a, _ in rows])
    return [_approval_out(a, names, can_decide_agent(p, a, r, roles[r.matter_id]), r) for a, r in rows]


async def decide_agent_action(
    db: AsyncSession, p: Principal, approval: Approval, body: Decision
) -> RunDetail:
    assert approval.agent_run_id is not None and approval.agent_step_id is not None
    try:
        run, role = await _run(db, p, approval.agent_run_id, lock=True)
    except NotFound:
        raise NotFound("Approval not found") from None
    if approval.status != "pending":
        raise Conflict(f"This approval has already been {approval.status}")
    if not can_decide_agent(p, approval, run, role):
        if approval.required_role == "matter_manager" and p.user_id == run.created_by:
            raise Forbidden("You started this agent, so the matter lead or another partner must approve this")
        who = (
            "someone who can edit the matter"
            if approval.required_role == "matter_editor"
            else ("the matter lead or a partner on the matter")
        )
        raise Forbidden(f"Only {who} can decide this")
    step = await db.get(AgentStep, approval.agent_step_id)
    if step is None:  # pragma: no cover - foreign key
        raise NotFound("Approval not found")
    comment = (body.comment or "").strip() or None
    now = _now()
    approval.status = "approved" if body.decision == "approve" else "rejected"
    approval.decided_by, approval.decided_at, approval.comment = p.user_id, now, comment
    names = await _names(db, [p.user_id])
    who = names.get(p.user_id, "the approver")
    step.result = {**step.result, "decision": approval.status, "decided_by": who, "comment": comment}
    if body.decision == "approve":
        step.status = "approved"
    else:
        step.status, step.finished_at = "rejected", now
        step.observation = f"{who} rejected this action." + (f" Their comment: {comment}" if comment else "")
    run.status, run.updated_at = "queued", now
    job = new_job(run)
    db.add(job)
    await db.flush()
    run.job_id = job.id
    await audit.record(
        db,
        p,
        f"agent.{approval.status}",
        "agent_run",
        run.id,
        run.matter_id,
        {"approval_id": str(approval.id), "step": step.position + 1, "tool": step.tool, "comment": comment},
    )
    return await run_detail(db, p, run.id)


# --------------------------------------------------------------------------- matter memory


def _memory_out(m: MemoryItem, names: dict[uuid.UUID, str], titles: dict[uuid.UUID, str]) -> MemoryOut:
    return MemoryOut(
        id=m.id,
        matter_id=m.matter_id,
        kind=m.kind,
        subject=m.subject,
        content=m.content,
        event_date=m.event_date,
        document_id=m.document_id,
        document_title=titles.get(m.document_id) if m.document_id else None,
        chunk_id=m.chunk_id,
        page_from=m.page_from,
        page_to=m.page_to,
        source=m.source,
        agent_run_id=m.agent_run_id,
        created_by=_person(names, m.created_by),
        created_at=m.created_at,
    )


async def _titles(db: AsyncSession, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return dict(
        (await db.execute(select(Document.id, Document.title).where(Document.id.in_(real)))).tuples().all()
    )


async def list_memory(
    db: AsyncSession, p: Principal, matter_id: uuid.UUID, q: str | None, kind: str | None
) -> list[MemoryOut]:
    await require_matter_access(db, p, matter_id)
    stmt = select(MemoryItem).where(MemoryItem.matter_id == matter_id, MemoryItem.deleted_at.is_(None))
    if kind:
        stmt = stmt.where(MemoryItem.kind == kind)
    if q:
        stmt = stmt.where(
            or_(
                text("matter_memory.tsv @@ plainto_tsquery('simple', :q)").bindparams(q=q),
                MemoryItem.subject.ilike(f"%{q}%"),
                MemoryItem.content.ilike(f"%{q}%"),
            )
        )
    items = list(
        (
            await db.scalars(
                stmt.order_by(MemoryItem.event_date.nulls_last(), MemoryItem.created_at).limit(500)
            )
        ).all()
    )
    names = await _names(db, [m.created_by for m in items])
    titles = await _titles(db, [m.document_id for m in items])
    return [_memory_out(m, names, titles) for m in items]


async def add_memory(db: AsyncSession, p: Principal, matter_id: uuid.UUID, body: MemoryIn) -> MemoryOut:
    role = await require_matter_access(db, p, matter_id)
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot change its memory")
    if body.document_id:
        doc = await db.get(Document, body.document_id)
        if doc is None or doc.matter_id != matter_id or doc.deleted_at is not None:
            raise Unprocessable("That document is not in this matter")
    m = MemoryItem(
        firm_id=p.firm_id,
        matter_id=matter_id,
        kind=body.kind,
        subject=body.subject.strip(),
        content=body.content.strip(),
        event_date=body.event_date,
        document_id=body.document_id,
        source="user",
        created_by=p.user_id,
    )
    db.add(m)
    await db.flush()
    await db.refresh(m)
    await audit.record(
        db, p, "memory.add", "matter_memory", m.id, matter_id, {"kind": m.kind, "subject": m.subject}
    )
    return _memory_out(m, await _names(db, [p.user_id]), await _titles(db, [m.document_id]))


async def delete_memory(db: AsyncSession, p: Principal, memory_id: uuid.UUID) -> None:
    m = await db.get(MemoryItem, memory_id)
    if m is None or m.deleted_at is not None:
        raise NotFound("Memory item not found")
    try:
        role = await require_matter_access(db, p, m.matter_id)
    except NotFound:
        raise NotFound("Memory item not found") from None
    if role not in EDIT_ROLES:
        raise Forbidden("Viewers on this matter cannot change its memory")
    m.deleted_at, m.deleted_by = _now(), p.user_id
    await audit.record(db, p, "memory.delete", "matter_memory", m.id, m.matter_id, {"subject": m.subject})
