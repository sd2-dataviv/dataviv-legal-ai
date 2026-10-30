"""AST-01..04, 06, 08..10, FND-05, FND-06: threads, streamed cited answers, verification, permissions,
history search, model policy, audit and export — end to end against PostgreSQL with the offline model."""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.llm import LLMError, Message, Notice, TextDelta, Usage, use_backend
from tests.conftest import World
from tests.test_vault_api import NOTICE_DOCX, ORDER_PDF, SPA_TXT, upload

RunJobs = Callable[[], Awaitable[int]]


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def matter_with_documents(
    client: AsyncClient, world: World, matter: dict[str, Any], run_jobs: RunJobs
) -> str:
    mid = str(matter["id"])
    await upload(
        client,
        mid,
        world.partner_a.headers,
        ("bail-order.pdf", ORDER_PDF),
        ("notice.docx", NOTICE_DOCX),
        ("spa.txt", SPA_TXT),
    )
    await run_jobs()
    return mid


async def new_thread(client: AsyncClient, mid: str, headers: dict[str, str]) -> str:
    r = await client.post(f"/v1/matters/{mid}/threads", json={}, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "New thread"
    return str(r.json()["id"])


async def ask(
    client: AsyncClient, tid: str, headers: dict[str, str], **body: Any
) -> list[tuple[str, dict[str, Any]]]:
    r = await client.post(f"/v1/threads/{tid}/messages", json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return events(r.text)


async def test_ask_streams_a_cited_verified_answer_and_stores_it(
    client: AsyncClient,
    world: World,
    new_matter: dict[str, Any],
    run_jobs: RunJobs,
    owner_engine: AsyncEngine,
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    h = world.partner_a.headers
    tid = await new_thread(client, mid, h)

    ev = await ask(client, tid, h, content="What is the notice period for termination?")
    kinds = [k for k, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done" and "delta" in kinds
    meta, done = ev[0][1], ev[-1][1]
    assert meta["sources"] and meta["sources"][0]["key"] == "S1"
    assert any(
        "TERMINATION" in (s["section_path"] or "") or "terminate" in s["excerpt"] for s in meta["sources"]
    )
    assert done["status"] == "complete" and done["model"] == "fake-1"
    assert done["sentences"] and all(s["cites"] for s in done["sentences"])
    assert done["sentences"][0]["label"] == "supported"
    assert done["verification"]["supported"] >= 1 and done["verification"]["verifier"] == "lexical"
    assert "[S" not in done["content"]  # stored without markers

    detail = (await client.get(f"/v1/threads/{tid}", headers=h)).json()
    assert detail["title"] == "What is the notice period for termination?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT action, detail::text FROM audit_events WHERE matter_id = :m ORDER BY id"),
                {"m": mid},
            )
        ).all()
    actions = [r[0] for r in rows]
    assert "assistant.ask" in actions and "ai.call" in actions
    call = json.loads(next(r[1] for r in rows if r[0] == "ai.call"))
    assert call["model"] == "fake-1" and len(call["prompt_sha256"]) == 64 and call["chunks"]


async def test_no_sources_means_no_model_call(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    tid = await new_thread(client, str(new_matter["id"]), world.partner_a.headers)
    ev = await ask(client, tid, world.partner_a.headers, content="What did the arbitrator award?")
    done = ev[-1][1]
    assert ev[0][1]["sources"] == [] and done["model"] is None and "could not find" in done["content"]
    assert done["verification"]["unsupported"] == 0


async def test_summarise_and_draft_modes_use_chosen_documents(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    h = world.partner_a.headers
    docs = (await client.get(f"/v1/matters/{mid}/documents", headers=h)).json()
    notice = next(d["id"] for d in docs if d["title"].startswith("notice"))
    tid = await new_thread(client, mid, h)

    ev = await ask(client, tid, h, content="Summarise", mode="summarise", document_ids=[notice])
    assert {s["document_id"] for s in ev[0][1]["sources"]} == {notice}
    assert ev[-1][1]["mode"] == "summarise" and ev[-1][1]["status"] == "complete"

    ev = await ask(
        client, tid, h, content="Reply refusing the payment demand", mode="draft", draft_kind="reply"
    )
    done = ev[-1][1]
    assert done["mode"] == "draft" and done["options"]["draft_kind"] == "reply"

    r = await client.get(f"/v1/threads/{tid}/messages/{done['id']}/export", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "wordprocessingml" in r.headers["content-type"]


async def test_threads_inherit_matter_permissions(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    tid = await new_thread(client, mid, world.partner_a.headers)
    body = {"content": "termination notice"}
    for outsider in (world.paralegal_a, world.partner_b):  # not a member / another firm
        assert (await client.get(f"/v1/threads/{tid}", headers=outsider.headers)).status_code == 404
        assert (
            await client.post(f"/v1/threads/{tid}/messages", json=body, headers=outsider.headers)
        ).status_code == 404
        assert (await client.get(f"/v1/matters/{mid}/threads", headers=outsider.headers)).status_code == 404

    a = world.associate_a
    r = await client.post(
        f"/v1/matters/{mid}/members",
        json={"user_id": str(a.id), "matter_role": "member"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 201, r.text
    assert (await client.get(f"/v1/threads/{tid}", headers=a.headers)).status_code == 200
    r = await client.post(
        f"/v1/matters/{mid}/walls",
        json={"user_id": str(a.id), "reason": "Acted for the other side"},
        headers=world.admin_a.headers,
    )
    assert r.status_code == 201, r.text
    assert (await client.post(f"/v1/threads/{tid}/messages", json=body, headers=a.headers)).status_code == 404


async def test_documents_from_another_matter_and_unknown_models_are_refused(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    h = world.partner_a.headers
    tid = await new_thread(client, str(new_matter["id"]), h)
    r = await client.post(
        f"/v1/threads/{tid}/messages", json={"content": "x y", "document_ids": [str(uuid.uuid4())]}, headers=h
    )
    assert r.status_code == 404
    r = await client.post(
        f"/v1/threads/{tid}/messages", json={"content": "x y", "model": "gpt-9-ultra"}, headers=h
    )
    assert r.status_code == 422 and "not permitted" in r.json()["detail"]


async def test_thread_history_is_searchable(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    h = world.partner_a.headers
    t1 = await new_thread(client, mid, h)
    await ask(client, t1, h, content="What does the legal notice demand?")
    t2 = await new_thread(client, mid, h)
    await ask(client, t2, h, content="Who was on the bench for the bail order?")
    found = (await client.get(f"/v1/matters/{mid}/threads", params={"q": "payment"}, headers=h)).json()
    assert [t["id"] for t in found] == [t1]  # matched in the answer text, not only the title
    all_threads = (await client.get(f"/v1/matters/{mid}/threads", headers=h)).json()
    assert {t["id"] for t in all_threads} >= {t1, t2} and all(t["message_count"] == 2 for t in all_threads)


class FailingBackend:
    name = "failing"

    def models(self) -> list[str]:
        return ["fake-1"]

    async def complete(
        self, model: str, messages: list[Message], *, max_tokens: int, json_schema: Any
    ) -> tuple[str, Usage]:
        raise LLMError("unused")

    async def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]:
        yield Notice("Groq rate limit reached; continuing in 2 s.")
        yield TextDelta("Partial answer")
        raise LLMError("Groq rate limit reached; try again in 40 s.", status=429, retry_after=40)


async def test_model_failure_is_reported_and_stored(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    tid = await new_thread(client, mid, world.partner_a.headers)
    with use_backend(FailingBackend()):
        ev = await ask(client, tid, world.partner_a.headers, content="termination notice period")
    kinds = [k for k, _ in ev]
    assert kinds == ["meta", "notice", "delta", "error"]
    err = ev[-1][1]
    assert err["status"] == 429 and err["retry_after"] == 40 and err["message"]["status"] == "failed"


async def test_model_verifier_option_runs_second_pass(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    tid = await new_thread(client, mid, world.partner_a.headers)
    s = get_settings()
    s.assistant_verifier = "model"
    try:
        ev = await ask(client, tid, world.partner_a.headers, content="termination notice period")
    finally:
        s.assistant_verifier = "lexical"
    assert ev[-1][1]["verification"]["verifier"] == "lexical+model"


async def test_models_endpoint_and_admin_only_policy(client: AsyncClient, world: World) -> None:
    r = await client.get("/v1/assistant/models", headers=world.associate_a.headers)
    assert r.status_code == 200 and r.json()["backend"] == "fake" and r.json()["allowed"] == ["fake-1"]
    body = {"allowed": ["fake-1"], "routes": {"draft": "fake-1"}}
    assert (
        await client.put("/v1/firm/model-policy", json=body, headers=world.partner_a.headers)
    ).status_code == 403
    r = await client.put("/v1/firm/model-policy", json=body, headers=world.admin_a.headers)
    assert r.status_code == 200 and r.json()["routes"] == {"draft": "fake-1"}
    bad = await client.put("/v1/firm/model-policy", json={"allowed": ["nope"]}, headers=world.admin_a.headers)
    assert bad.status_code == 422
    # other firm unaffected
    assert (await client.get("/v1/assistant/models", headers=world.partner_b.headers)).json()["routes"] == {}
