"""AGT-01..07: agents end to end with the offline model — the loop, tools, approvals, budgets, scope,
memory, subagents and the trace."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import llm
from app.core.llm import Message
from app.modules.agents.runtime import DECISION_TITLE, FINAL_TITLE
from app.modules.agents.spec import SpecError, builtins, validate
from app.modules.agents.tools import REGISTRY, check_arguments, parameters
from tests.conftest import World
from tests.test_workflows_api import setup

RunJobs = Callable[[], Awaitable[int]]
TERMINATION = "How can the agreement be terminated and with how much notice?"


async def start(client: AsyncClient, mid: str, h: dict[str, str], agent: str, goal: str, **kw: Any) -> Any:
    return await client.post(
        f"/v1/matters/{mid}/agent-runs", json={"agent": agent, "goal": goal, **kw}, headers=h
    )


async def get_run(client: AsyncClient, rid: str, h: dict[str, str]) -> dict[str, Any]:
    r = await client.get(f"/v1/agent-runs/{rid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def tools_used(run: dict[str, Any]) -> list[str]:
    return [s["tool"] for s in run["steps"]]


# --------------------------------------------------------------------------- definitions (no database)


def test_builtins_registry_and_validation() -> None:
    assert set(builtins()) == {
        "matter-researcher",
        "chronology-builder",
        "notice-reply",
        "due-diligence",
        "document-analyst",
    }
    assert "query: string (required)" in parameters(REGISTRY["search_documents"])
    with pytest.raises(ValueError, match="query"):
        check_arguments("search_documents", {"query": ""})
    with pytest.raises(ValueError, match=r"(?i)extra"):
        check_arguments("final_answer", {"answer": "x", "matter_id": "another"})  # no widening scope
    bad = {
        "slug": "x",
        "name": "",
        "instructions": "short",
        "tools": ["search_documents", "send_email"],
        "approval": {"search_documents": "auto", "create_draft": "sometimes"},
        "budget": {"max_steps": 99, "max_cost_usd": 5},
    }
    with pytest.raises(SpecError) as e:
        validate(bad)
    joined = " | ".join(e.value.problems)
    for expected in (
        "slug",
        "name is required",
        "instructions",
        "send_email",
        "not a write tool",
        "max_steps",
        "max_cost_usd",
    ):
        assert expected in joined, expected
    with pytest.raises(SpecError, match="subagent"):
        validate(
            {**builtins()["due-diligence"], "subagent": "chronology-builder"}
        )  # write tools: not allowed


# --------------------------------------------------------------------------- the loop


async def test_researcher_answers_with_verified_citations_and_a_full_trace(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    owner_engine: AsyncEngine,
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    agents = (await client.get("/v1/agents", headers=h)).json()
    assert {a["slug"] for a in agents} >= {
        "matter-researcher",
        "chronology-builder",
        "notice-reply",
        "due-diligence",
    }
    assert "document-analyst" not in {a["slug"] for a in agents}  # subagent only

    r = await start(client, mid, h, "matter-researcher", TERMINATION)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "queued" and run["max_steps"] == 8 and run["can_cancel"] is True
    await run_jobs()
    run = await get_run(client, run["id"], h)
    assert run["status"] == "succeeded", run["error"]
    assert tools_used(run) == ["search_documents", "final_answer"]
    search = run["steps"][0]
    assert search["thought"] and search["arguments"]["query"] and "[S1]" in search["observation"]
    assert search["model"] and search["input_tokens"] > 0
    assert "[S1]" in run["answer"] and run["sources"][0]["key"] == "S1" and run["sources"][0]["excerpt"]
    assert run["verification"]["supported"] >= 1 and run["verification"]["unsupported"] == 0
    assert run["steps_used"] == 2 and run["input_tokens"] > 0
    listed = (await client.get(f"/v1/matters/{mid}/agent-runs", headers=h)).json()
    assert listed[0]["id"] == run["id"] and listed[0]["status"] == "succeeded"

    async with owner_engine.connect() as conn:  # AGT-07: the trace is in the audit log too
        actions = (
            (
                await conn.execute(
                    text("SELECT action FROM audit_events WHERE object_id = :r ORDER BY id"), {"r": run["id"]}
                )
            )
            .scalars()
            .all()
        )
    assert actions[0] == "agent.start" and "agent.step" in actions and "ai.call" in actions
    assert actions[-1] == "agent.succeeded"


async def test_auto_writes_run_and_confirm_writes_wait_for_a_person(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    rid = (await start(client, mid, h, "chronology-builder", "Termination notice period and dates")).json()[
        "id"
    ]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "waiting_approval", run["error"]
    assert tools_used(run) == ["search_documents", "remember_fact", "create_draft"]
    assert run["steps"][1]["status"] == "done" and run["steps"][2]["status"] == "waiting_approval"

    memory = (await client.get(f"/v1/matters/{mid}/memory", headers=h)).json()
    saved = next(m for m in memory if m["agent_run_id"] == rid)
    assert saved["source"] == "agent" and saved["chunk_id"] and saved["page_from"] and saved["document_title"]

    approval = run["approvals"][0]
    assert approval["subject"] == "agent" and approval["detail"]["tool"] == "create_draft"
    assert approval["can_decide"] is True  # "confirm": the person who started it may confirm
    queue = (await client.get("/v1/approvals?mine=true", headers=h)).json()
    assert approval["id"] in {a["id"] for a in queue}
    viewer = await client.post(
        f"/v1/approvals/{approval['id']}/decision",
        json={"decision": "approve"},
        headers=world.paralegal_a.headers,
    )
    assert viewer.status_code == 403
    r = await client.post(f"/v1/approvals/{approval['id']}/decision", json={"decision": "approve"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued" and r.json()["steps"][2]["status"] == "approved"

    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "succeeded", run["error"]
    did = run["steps"][2]["result"]["draft_id"]
    draft = (await client.get(f"/v1/drafts/{did}", headers=h)).json()
    assert draft["status"] == "draft" and "[1]" in draft["content"] and draft["sources"]


async def test_manager_policy_and_a_rejection_the_agent_adapts_to(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    rid = (await start(client, mid, h, "notice-reply", "Reply to the legal notice demanding payment")).json()[
        "id"
    ]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "waiting_approval", run["error"]
    approval = run["approvals"][0]
    assert approval["required_role"] == "matter_manager" and approval["can_decide"] is False
    r = await client.post(f"/v1/approvals/{approval['id']}/decision", json={"decision": "approve"}, headers=h)
    assert r.status_code == 403 and "started this agent" in r.text
    r = await client.post(
        f"/v1/approvals/{approval['id']}/decision",
        json={"decision": "reject", "comment": "Do not draft yet; list the points first"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 200, r.text
    step = r.json()["steps"][-1]
    assert step["status"] == "rejected" and "list the points first" in step["observation"]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "succeeded", run["error"]  # the agent carried on without the draft
    assert "draft_id" not in run["steps"][-2]["result"] and run["steps"][-1]["tool"] == "final_answer"
    assert (
        await client.post(
            f"/v1/approvals/{approval['id']}/decision",
            json={"decision": "approve"},
            headers=world.partner_a.headers,
        )
    ).status_code == 409


# --------------------------------------------------------------------------- budgets and bad moves


async def test_budgets_stop_the_loop(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    one = (await start(client, mid, h, "matter-researcher", TERMINATION, max_steps=1)).json()
    assert one["max_steps"] == 1
    big = await start(client, mid, h, "matter-researcher", TERMINATION, max_steps=30)
    assert big.json()["max_steps"] == 8  # a run can lower the agent's budget, never raise it

    def stubborn(messages: list[Message]) -> dict[str, Any]:
        return {
            "thought": "Search again.",
            "tool": "search_documents",
            "arguments": json.dumps({"query": "x y"}),
        }

    await run_jobs()  # the 1-step run: only final_answer was allowed
    first = await get_run(client, one["id"], h)
    assert first["status"] == "succeeded" and tools_used(first) == ["final_answer"]

    monkeypatch.setitem(llm._FAKE_STRUCTURED, DECISION_TITLE, stubborn)
    monkeypatch.setitem(llm._FAKE_STRUCTURED, FINAL_TITLE, stubborn)  # it will not answer even when told to
    rid = (await start(client, mid, h, "matter-researcher", TERMINATION, max_steps=3)).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "stopped" and "3 steps" in run["error"] and run["steps_used"] == 3
    assert (
        run["steps"][-1]["status"] == "failed" and "not one of your tools" in run["steps"][-1]["observation"]
    )


async def test_bad_arguments_and_unknown_passages_become_observations(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    moves = iter(
        [
            ("search_documents", {"query": ""}),
            ("read_passage", {"source": "S99"}),
            ("search_documents", {"query": "x", "matter_id": "somewhere-else"}),
            ("final_answer", {"answer": "Nothing found."}),
        ]
    )

    def scripted(messages: list[Message]) -> dict[str, Any]:
        tool, args = next(moves)
        return {"thought": "Try.", "tool": tool, "arguments": json.dumps(args)}

    monkeypatch.setitem(llm._FAKE_STRUCTURED, DECISION_TITLE, scripted)
    rid = (await start(client, mid, h, "matter-researcher", TERMINATION)).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "succeeded"
    s = run["steps"]
    assert s[0]["status"] == "failed" and "Invalid arguments" in s[0]["observation"]
    assert s[1]["status"] == "failed" and "S99 is not a passage you have seen" in s[1]["observation"]
    assert s[2]["status"] == "failed" and "extra" in s[2]["observation"].lower()


# --------------------------------------------------------------------------- permissions and scope


async def test_agents_act_only_as_their_user_inside_the_matter(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    owner_engine: AsyncEngine,
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    assert (
        await start(client, mid, world.paralegal_a.headers, "matter-researcher", TERMINATION)
    ).status_code == 403
    assert (
        await start(client, mid, world.partner_b.headers, "matter-researcher", TERMINATION)
    ).status_code == 404
    r = await start(
        client,
        mid,
        h,
        "matter-researcher",
        TERMINATION,
        document_ids=["00000000-0000-4000-8000-00000000abcd"],
    )
    assert r.status_code == 422
    assert (await start(client, mid, h, "document-analyst", TERMINATION)).status_code == 422

    # scoped to one document: the search only sees that document
    rid = (
        await start(client, mid, h, "matter-researcher", TERMINATION, document_ids=[docs["notice.docx"]])
    ).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["scope_document_ids"] == [docs["notice.docx"]]
    assert all(src["document_id"] == docs["notice.docx"] for src in run["sources"])
    assert (await client.get(f"/v1/agent-runs/{rid}", headers=world.partner_b.headers)).status_code == 404
    assert (await client.get(f"/v1/agent-runs/{rid}", headers=world.admin_a.headers)).status_code == 404

    # the person who started it leaves the matter: the agent stops at its next step
    rid = (await start(client, mid, h, "matter-researcher", TERMINATION)).json()["id"]
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM matter_members WHERE matter_id = :m AND user_id = :u"),
            {"m": mid, "u": world.associate_a.id},
        )
    await run_jobs()
    run = await get_run(client, rid, world.partner_a.headers)
    assert run["status"] == "failed" and "no longer works on the matter" in run["error"]


# --------------------------------------------------------------------------- subagents


async def test_orchestrator_delegates_one_subagent_per_document(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    rid = (
        await start(client, mid, h, "due-diligence", "Termination rights, notice and payment demands")
    ).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, h)
    assert run["status"] == "succeeded", run["error"]
    assert tools_used(run) == ["list_documents", "delegate_to_subagents", "final_answer"]
    assert len(run["children"]) == 3 and all(c["status"] == "succeeded" for c in run["children"])
    delegate = run["steps"][1]
    assert delegate["status"] == "done" and delegate["observation"].count("## ") == 3
    assert len(delegate["result"]["children"]) == 3
    assert run["cost_usd"] >= sum(c["cost_usd"] for c in run["children"])
    assert "[S" in run["answer"] and run["verification"]["unsupported"] == 0
    keys = {s["key"] for s in run["sources"]}
    assert all(k in keys for st in run["sentences"] for k in st["cites"])  # subagent ids renumbered

    child = await get_run(client, run["children"][0]["id"], h)
    assert (
        child["parent_run_id"] == rid
        and len(child["scope_document_ids"]) == 1
        and child["can_cancel"] is False
    )
    assert (await client.post(f"/v1/agent-runs/{child['id']}/cancel", headers=h)).status_code == 409
    listed = (await client.get(f"/v1/matters/{mid}/agent-runs", headers=h)).json()
    assert [r["id"] for r in listed if r["id"] == child["id"]] == []  # only top-level runs are listed
    assert docs  # three documents were uploaded


async def test_cancelling_a_waiting_run_cancels_its_approval(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    rid = (await start(client, mid, h, "notice-reply", "Reply to the legal notice demanding payment")).json()[
        "id"
    ]
    await run_jobs()
    r = await client.post(f"/v1/agent-runs/{rid}/cancel", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    approval = r.json()["approvals"][0]
    assert approval["status"] == "cancelled"
    assert (
        await client.post(
            f"/v1/approvals/{approval['id']}/decision",
            json={"decision": "approve"},
            headers=world.partner_a.headers,
        )
    ).status_code == 409
    assert (await client.post(f"/v1/agent-runs/{rid}/cancel", headers=h)).status_code == 409
    await run_jobs()
    assert (await get_run(client, rid, h))["status"] == "cancelled"


# --------------------------------------------------------------------------- memory and firm agents


async def test_matter_memory_is_permission_checked(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, docs = await setup(client, world, new_matter, run_jobs)
    h = world.associate_a.headers
    r = await client.post(
        f"/v1/matters/{mid}/memory",
        headers=h,
        json={
            "kind": "deadline",
            "subject": "Reply to notice",
            "content": "Reply due within 15 days of receipt",
            "event_date": "2026-11-01",
            "document_id": docs["notice.docx"],
        },
    )
    assert r.status_code == 201, r.text
    item = r.json()
    assert (
        item["source"] == "user"
        and item["document_title"]
        and item["created_by"]["id"] == str(world.associate_a.id)
    )
    assert (
        await client.post(
            f"/v1/matters/{mid}/memory",
            headers=world.paralegal_a.headers,
            json={"subject": "x y", "content": "z w"},
        )
    ).status_code == 403
    assert (await client.get(f"/v1/matters/{mid}/memory", headers=world.partner_b.headers)).status_code == 404
    found = (await client.get(f"/v1/matters/{mid}/memory?q=notice", headers=world.paralegal_a.headers)).json()
    assert [m["id"] for m in found] == [item["id"]]
    assert (await client.get(f"/v1/matters/{mid}/memory?kind=event", headers=h)).json() == []

    rid = (
        await start(client, mid, h, "matter-researcher", "What deadline applies to the reply to notice?")
    ).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, h)  # recall_memory is available to the researcher
    assert run["status"] == "succeeded"

    assert (
        await client.delete(f"/v1/memory/{item['id']}", headers=world.paralegal_a.headers)
    ).status_code == 403
    assert (await client.delete(f"/v1/memory/{item['id']}", headers=h)).status_code == 204
    assert (await client.get(f"/v1/matters/{mid}/memory", headers=h)).json() == []
    assert (await client.delete(f"/v1/memory/{item['id']}", headers=h)).status_code == 404


CUSTOM = {
    "slug": "clause-finder",
    "name": "Clause finder",
    "description": "Finds a clause and quotes it.",
    "instructions": "Find the clause the lawyer asks about and quote it exactly, with its citation.",
    "tools": ["search_documents", "read_passage"],
    "budget": {"max_steps": 4, "max_cost_usd": 0.01},
}


async def test_partners_publish_versioned_agents(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid, _ = await setup(client, world, new_matter, run_jobs)
    p, a = world.partner_a.headers, world.associate_a.headers
    assert (await client.post("/v1/agents/validate", json={"spec": {"slug": "x"}}, headers=a)).json()[
        "valid"
    ] is False
    assert (await client.post("/v1/agents", json={"spec": CUSTOM}, headers=a)).status_code == 403
    assert (
        await client.post("/v1/agents", json={"spec": {**CUSTOM, "tools": []}}, headers=p)
    ).status_code == 422
    assert (
        await client.post("/v1/agents", json={"spec": {**CUSTOM, "slug": "matter-researcher"}}, headers=p)
    ).status_code == 409
    v1 = await client.post("/v1/agents", json={"spec": CUSTOM}, headers=p)
    assert v1.status_code == 201, v1.text
    v2 = (
        await client.post("/v1/agents", json={"spec": {**CUSTOM, "name": "Clause finder v2"}}, headers=p)
    ).json()
    assert v2["version"] == v1.json()["version"] + 1
    assert (await client.get("/v1/agents/clause-finder", headers=a)).json()["name"] == "Clause finder v2"
    rid = (await start(client, mid, a, "clause-finder", TERMINATION)).json()["id"]
    await run_jobs()
    run = await get_run(client, rid, a)
    assert run["status"] == "succeeded" and run["version"] == v2["version"] and run["max_steps"] == 4
