"""WFL-01..07: workflows end to end with the offline model — definitions, runs, approvals, playbooks."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.workflows import engine, steps
from app.modules.workflows.spec import SpecError, render, validate
from app.modules.workflows.templates import PLAYBOOK_TEMPLATES, builtins
from tests.conftest import World
from tests.test_drafts_api import team
from tests.test_vault_api import NOTICE_DOCX, ORDER_PDF, upload

RunJobs = Callable[[], Awaitable[int]]

SPA = (
    b"SHARE PURCHASE AGREEMENT\n\nThis Agreement is made between Lotus Holdings (the Seller) and Zenith LLP "
    b"(the Buyer) for a consideration of Rs. 10,00,00,000.\n\n9. LIABILITY\n9.1 The liability of the Seller "
    b"under this Agreement is capped at 10% of the consideration.\n\n12. TERMINATION\n12.1 Either party may "
    b"terminate this Agreement by giving thirty days written notice.\n\n14. GOVERNING LAW\n14.1 This Agreement "
    b"is governed by the laws of India.\n\n15. DISPUTES\n15.1 Disputes shall be referred to arbitration seated "
    b"in Singapore.\n"
)


async def setup(
    client: AsyncClient, world: World, matter: dict[str, Any], run_jobs: RunJobs
) -> tuple[str, dict[str, str]]:
    """Partner A leads, associate A is a member, paralegal A a viewer; three documents indexed."""
    mid = await team(client, world, matter)
    res = await upload(
        client,
        mid,
        world.partner_a.headers,
        ("order.pdf", ORDER_PDF),
        ("notice.docx", NOTICE_DOCX),
        ("spa.txt", SPA),
    )
    await run_jobs()
    return mid, {f["filename"]: f["document_id"] for f in res["files"]}


async def start(client: AsyncClient, mid: str, h: dict[str, str], workflow: str, **inputs: Any) -> Any:
    return await client.post(
        f"/v1/matters/{mid}/workflow-runs", json={"workflow": workflow, "inputs": inputs}, headers=h
    )


async def get_run(client: AsyncClient, rid: str, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get(f"/v1/workflow-runs/{rid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def step(run: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in run["steps"] if s["key"] == key)


async def playbook(client: AsyncClient, world: World) -> dict[str, Any]:
    r = await client.post(
        "/v1/playbooks", json={"from_template": "spa-buyer"}, headers=world.partner_a.headers
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- definitions (no database)


def test_builtins_and_templates_are_valid() -> None:
    assert set(builtins()) == {"contract-review", "legal-notice", "order-brief"}
    assert [len(t["clauses"]) for t in PLAYBOOK_TEMPLATES] == [6, 5]


def test_spec_validation_lists_every_problem() -> None:
    bad = {
        "slug": "Bad Slug",
        "name": "",
        "inputs": [{"key": "doc", "type": "file"}],
        "steps": [
            {"id": "a", "type": "llm", "instructions": "Use {{inputs.missing}} and {{steps.later.text}}"},
            {"id": "a", "type": "approval", "role": "anyone"},
            {"id": "later", "type": "output_document", "title": "x", "from_step": "nope", "kind": "poem"},
        ],
    }
    with pytest.raises(SpecError) as e:
        validate(bad)
    text_ = " | ".join(e.value.problems)
    for expected in (
        "slug",
        "name is required",
        "type must be one of",
        "inputs.missing",
        "steps.later",
        "id is repeated",
        "role must be",
        "from_step",
        "kind must be",
    ):
        assert expected in text_, expected


def test_render_keeps_types_for_whole_references() -> None:
    ctx = {
        "inputs": {"docs": ["a", "b"], "n": 15},
        "steps": {"s": {"passages": [{"text": "x"}, {"text": "y"}]}},
    }
    assert render("{{inputs.docs}}", ctx) == ["a", "b"]
    assert render("Within {{inputs.n}} days", ctx) == "Within 15 days"
    assert render({"q": ["{{steps.s.passages}}"]}, ctx) == {"q": [[{"text": "x"}, {"text": "y"}]]}
    assert render("Facts: {{steps.s.passages}}", ctx) == "Facts: x\ny"


# --------------------------------------------------------------------------- runs


async def test_order_brief_runs_to_a_cited_draft(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    listing = (await client.get("/v1/workflows", headers=h)).json()
    assert {w["slug"] for w in listing} >= {"contract-review", "legal-notice", "order-brief"}

    r = await start(client, mid, h, "order-brief", order=docs["order.pdf"])
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "queued" and [s["status"] for s in run["steps"]] == ["pending"] * 3
    assert run["job"]["status"] == "queued" and run["can_cancel"] is True

    await run_jobs()
    run = await get_run(client, run["id"], h)
    assert run["status"] == "succeeded", run["error"]
    assert [s["status"] for s in run["steps"]] == ["done", "done", "done"]
    facts = step(run, "facts")["output"]
    assert facts["document_title"] and set(facts["fields"]) >= {"court", "case_number", "outcome"}
    assert facts["fields"]["court"]["status"] == "done" and "HIGH COURT" in facts["fields"]["court"]["answer"]
    assert facts["passages"] and all(p["chunk_id"] and p["page_from"] for p in facts["passages"])
    brief = step(run, "brief")
    assert brief["output"]["sentences"] and brief["model"] and brief["input_tokens"] > 0
    assert brief["output"]["verification"]["verifier"] == "lexical"
    assert run["input_tokens"] >= brief["input_tokens"] + step(run, "facts")["input_tokens"]

    did = step(run, "deliver")["output"]["draft_id"]
    draft = (await client.get(f"/v1/drafts/{did}", headers=h)).json()
    assert draft["kind"] == "memo" and draft["status"] == "draft" and draft["title"].startswith("Brief: ")
    assert "[1]" in draft["content"] and draft["sources"][0]["document_id"] == docs["order.pdf"]
    listed = (await client.get(f"/v1/matters/{mid}/workflow-runs", headers=h)).json()
    assert listed[0]["id"] == run["id"] and listed[0]["steps_done"] == 3 == listed[0]["steps_total"]


async def test_contract_review_waits_for_approval_then_delivers_an_approved_memo(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    book = await playbook(client, world)
    h = world.associate_a.headers
    r = await start(client, mid, h, "contract-review", agreement=docs["spa.txt"], playbook=book["id"])
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["inputs"]["our_side"] == "Buyer"  # the default
    await run_jobs()

    run = await get_run(client, rid, h)
    assert run["status"] == "waiting_approval", run["error"]
    assert [s["status"] for s in run["steps"]] == ["done", "done", "waiting", "pending"]
    review = step(run, "review")["output"]
    assert review["playbook_name"] == book["name"] and len(review["clauses"]) == 6
    by_key = {c["key"]: c for c in review["clauses"]}
    assert by_key["termination"]["position"] == "fallback" and by_key["termination"]["quote"]
    assert by_key["stamp_duty"]["position"] == "missing" and by_key["stamp_duty"]["suggested_wording"]
    assert sum(review["counts"].values()) == 6
    assert "playbook_snapshot" not in step(run, "review")["resolved"]  # internal, not shown
    memo = step(run, "memo")["output"]
    assert memo["kind"] == "memo" and memo["sentences"]

    # the person who started it cannot approve; the lead can, and sees it in their queue
    approval = run["approvals"][0]
    assert approval["status"] == "pending" and approval["can_decide"] is False
    r = await client.post(f"/v1/approvals/{approval['id']}/decision", json={"decision": "approve"}, headers=h)
    assert r.status_code == 403
    lead = world.partner_a.headers
    queue = (await client.get("/v1/approvals?mine=true", headers=lead)).json()
    assert approval["id"] in {a["id"] for a in queue}
    assert approval["id"] not in {
        a["id"] for a in (await client.get("/v1/approvals?mine=true", headers=h)).json()
    }
    r = await client.post(
        f"/v1/approvals/{approval['id']}/decision", json={"decision": "reject"}, headers=lead
    )
    assert r.status_code == 422  # a rejection needs a reason
    r = await client.post(
        f"/v1/approvals/{approval['id']}/decision",
        json={"decision": "approve", "comment": "Fine to send"},
        headers=lead,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    again = await client.post(
        f"/v1/approvals/{approval['id']}/decision", json={"decision": "approve"}, headers=lead
    )
    assert again.status_code == 409

    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "succeeded" and step(run, "sign_off")["output"]["decided_by_id"] == str(
        world.partner_a.id
    )
    draft = (await client.get(f"/v1/drafts/{step(run, 'deliver')['output']['draft_id']}", headers=h)).json()
    assert draft["status"] == "approved" and draft["approved_version_no"] == 1
    assert draft["title"].startswith("Contract review: ")


async def test_rejection_stops_the_run_and_nothing_is_delivered(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    r = await start(
        client,
        mid,
        h,
        "legal-notice",
        recipient="Zenith LLP, New Delhi",
        demand="payment of Rs. 5,00,000 due to our client",
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["inputs"]["deadline_days"] == 15
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "waiting_approval", run["error"]
    assert step(run, "facts")["output"]["count"] >= 1
    assert step(run, "notice")["output"]["kind"] == "notice"

    r = await client.post(
        f"/v1/approvals/{run['approvals'][0]['id']}/decision",
        json={"decision": "reject", "comment": "Add the invoice dates"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "rejected" and "Add the invoice dates" in run["error"]
    assert step(run, "deliver")["status"] == "skipped" and run["can_retry"] is False
    await run_jobs()
    assert (await get_run(client, rid, h))["status"] == "rejected"


async def test_inputs_are_checked_before_a_run_starts(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    r = await start(client, mid, h, "legal-notice", recipient="X", demand="Pay", deadline_days=200)
    assert r.status_code == 422 and "between 1 and 90" in r.text
    r = await start(client, mid, h, "legal-notice", demand="Pay")
    assert r.status_code == 422 and "required" in r.text
    r = await start(client, mid, h, "order-brief", order=docs["order.pdf"], colour="blue")
    assert r.status_code == 422 and "no such input" in r.text
    r = await start(client, mid, h, "order-brief", order="00000000-0000-4000-8000-00000000abcd")
    assert r.status_code == 422 and "not a document in this matter" in r.text
    r = await start(
        client,
        mid,
        h,
        "contract-review",
        agreement=docs["spa.txt"],
        playbook="00000000-0000-4000-8000-00000000abcd",
    )
    assert r.status_code == 422 and "playbook not found" in r.text
    r = await start(client, mid, h, "no-such-workflow")
    assert r.status_code == 404
    r = await start(client, mid, world.paralegal_a.headers, "order-brief", order=docs["order.pdf"])
    assert r.status_code == 403  # viewers read, they do not start
    r = await start(client, mid, world.partner_b.headers, "order-brief", order=docs["order.pdf"])
    assert r.status_code == 404  # another firm: not even its existence


async def test_other_firms_and_non_members_cannot_see_runs(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    rid = (
        await start(client, mid, world.associate_a.headers, "order-brief", order=docs["order.pdf"])
    ).json()["id"]
    assert (await client.get(f"/v1/workflow-runs/{rid}", headers=world.partner_b.headers)).status_code == 404
    assert (await client.get(f"/v1/workflow-runs/{rid}", headers=world.admin_a.headers)).status_code == 404
    viewer = await get_run(client, rid, world.paralegal_a.headers)
    assert viewer["can_cancel"] is False
    r = await client.post(f"/v1/workflow-runs/{rid}/cancel", headers=world.paralegal_a.headers)
    assert r.status_code == 403
    await run_jobs()  # leave no queued run behind for the tests that patch the engine


async def test_a_failed_step_is_retried_without_repeating_finished_steps(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    real = steps.HANDLERS["llm"]
    calls = {"n": 0}

    async def flaky(ctx: steps.StepContext) -> steps.StepResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise steps.StepFailed("The model provider is down")
        return await real(ctx)

    monkeypatch.setitem(steps.HANDLERS, "llm", flaky)
    rid = (await start(client, mid, h, "order-brief", order=docs["order.pdf"])).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "failed" and "provider is down" in run["error"] and run["can_retry"] is True
    assert [s["status"] for s in run["steps"]] == ["done", "failed", "skipped"]
    facts_finished = step(run, "facts")["finished_at"]

    r = await client.post(f"/v1/workflow-runs/{rid}/retry", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "succeeded" and calls["n"] == 2
    assert step(run, "facts")["finished_at"] == facts_finished and step(run, "facts")["attempts"] == 1
    assert (await client.post(f"/v1/workflow-runs/{rid}/retry", headers=h)).status_code == 409


async def test_a_crash_mid_step_resumes_when_the_job_is_reclaimed(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    real_finish = engine._finish

    async def die(*args: Any, **kwargs: Any) -> None:
        return None  # the worker "dies" after starting a step: nothing is recorded

    monkeypatch.setattr(engine, "_finish", die)
    run = (await start(client, mid, h, "order-brief", order=docs["order.pdf"])).json()
    await run_jobs()
    stuck = await get_run(client, run["id"], h)
    assert step(stuck, "facts")["status"] == "running" and stuck["job"]["status"] == "running"

    monkeypatch.setattr(engine, "_finish", real_finish)
    async with owner_engine.begin() as conn:  # ten minutes pass
        await conn.execute(
            text("UPDATE jobs SET updated_at = now() - interval '11 minutes' WHERE id = :j"),
            {"j": run["job"]["id"]},
        )
    await run_jobs()
    done = await get_run(client, run["id"], h)
    assert done["status"] == "succeeded" and step(done, "facts")["attempts"] == 2


async def test_cancelling_a_waiting_run_cancels_its_approval(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    rid = await start(
        client, mid, h, "legal-notice", recipient="Zenith LLP", demand="payment of Rs. 5,00,000"
    )
    rid = rid.json()["id"]
    await run_jobs()
    r = await client.post(f"/v1/workflow-runs/{rid}/cancel", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (
        r.json()["approvals"][0]["status"] == "cancelled" and step(r.json(), "deliver")["status"] == "skipped"
    )
    r = await client.post(
        f"/v1/approvals/{r.json()['approvals'][0]['id']}/decision",
        json={"decision": "approve"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 409
    assert (await client.post(f"/v1/workflow-runs/{rid}/cancel", headers=h)).status_code == 409


# --------------------------------------------------------------------------- firm definitions


CUSTOM = {
    "slug": "rent-roll",
    "name": "Rent roll",
    "description": "Rent from every lease, then a short note.",
    "inputs": [{"key": "docs", "label": "Leases", "type": "documents", "required": True}],
    "steps": [
        {
            "id": "table",
            "type": "review_table",
            "title": "Read the agreements",
            "documents": "{{inputs.docs}}",
            "columns": [
                {"label": "Termination", "question": "How can a party terminate, with how much notice?"},
                {"label": "Law", "question": "Which law governs the agreement?"},
            ],
        },
        {
            "id": "note",
            "type": "llm",
            "task": "qa",
            "title": "Summarise",
            "sources": ["table"],
            "context": ["table"],
            "instructions": "Summarise the termination terms.",
        },
    ],
}


async def test_partners_publish_versioned_workflows_and_review_table_steps_work(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    p, a = world.partner_a.headers, world.associate_a.headers
    check = (await client.post("/v1/workflows/validate", json={"spec": {"slug": "x"}}, headers=a)).json()
    assert check["valid"] is False and check["problems"]
    assert (await client.post("/v1/workflows", json={"spec": CUSTOM}, headers=a)).status_code == 403
    bad = {**CUSTOM, "steps": [{"id": "x", "type": "teleport"}]}
    assert (await client.post("/v1/workflows", json={"spec": bad}, headers=p)).status_code == 422
    clash = {**CUSTOM, "slug": "order-brief"}
    assert (await client.post("/v1/workflows", json={"spec": clash}, headers=p)).status_code == 409
    v1 = await client.post("/v1/workflows", json={"spec": CUSTOM}, headers=p)
    assert v1.status_code == 201, v1.text
    v2 = (
        await client.post("/v1/workflows", json={"spec": {**CUSTOM, "name": "Rent roll v2"}}, headers=p)
    ).json()
    assert v2["version"] == v1.json()["version"] + 1
    assert (await client.get("/v1/workflows/rent-roll", headers=a)).json()["name"] == "Rent roll v2"

    r = await start(client, mid, a, "rent-roll", docs=[docs["spa.txt"], docs["notice.docx"]])
    assert r.status_code == 201, r.text
    assert r.json()["version"] == v2["version"]
    await run_jobs()
    run = await get_run(client, r.json()["id"], a)
    assert run["status"] == "succeeded", run["error"]
    table = step(run, "table")["output"]
    assert table["documents"] == 2 and table["columns"] == ["Termination", "Law"]
    grid = (await client.get(f"/v1/review-tables/{table['table_id']}", headers=a)).json()
    assert grid["totals"]["queued"] == 0 and grid["rows"]
    assert step(run, "note")["output"]["sentences"]


async def test_playbooks_are_managed_by_partners_and_versioned(client: AsyncClient, world: World) -> None:
    p, a = world.partner_a.headers, world.associate_a.headers
    templates = (await client.get("/v1/playbook-templates", headers=a)).json()
    assert {t["id"] for t in templates} == {"spa-buyer", "lease-lessee"}
    assert (
        await client.post("/v1/playbooks", json={"from_template": "spa-buyer"}, headers=a)
    ).status_code == 403
    book = await playbook(client, world)
    assert book["version"] == 1 and len(book["clauses"]) == 6
    body = {
        "name": book["name"] + " (2026)",
        "contract_type": book["contract_type"],
        "clauses": book["clauses"][:2],
    }
    r = await client.put(f"/v1/playbooks/{book['id']}", json=body, headers=p)
    assert r.status_code == 200 and r.json()["version"] == 2 and len(r.json()["clauses"]) == 2
    dup = {**body, "clauses": [book["clauses"][0], book["clauses"][0]]}
    assert (await client.put(f"/v1/playbooks/{book['id']}", json=dup, headers=p)).status_code == 422
    assert book["id"] in {b["id"] for b in (await client.get("/v1/playbooks", headers=a)).json()}
    assert (
        await client.get(f"/v1/playbooks/{book['id']}", headers=world.partner_b.headers)
    ).status_code == 404
    assert (await client.delete(f"/v1/playbooks/{book['id']}", headers=p)).status_code == 204
    assert (await client.get(f"/v1/playbooks/{book['id']}", headers=a)).status_code == 404
