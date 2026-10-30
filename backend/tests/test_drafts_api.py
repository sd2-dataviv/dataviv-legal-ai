"""DRF-01..07: editable, versioned, approvable drafts shared by a matter's members — end to end."""

import io
import zipfile
from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from tests.conftest import World
from tests.test_assistant_api import ask, matter_with_documents, new_thread

RunJobs = Callable[[], Awaitable[int]]


async def team(client: AsyncClient, world: World, matter: dict[str, Any]) -> str:
    """Partner A leads; associate A edits (member); paralegal A only reads (viewer)."""
    mid = str(matter["id"])
    for user, role in ((world.associate_a, "member"), (world.paralegal_a, "viewer")):
        r = await client.post(
            f"/v1/matters/{mid}/members",
            json={"user_id": str(user.id), "matter_role": role},
            headers=world.partner_a.headers,
        )
        assert r.status_code == 201, r.text
    return mid


async def create(client: AsyncClient, mid: str, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    r = await client.post(f"/v1/matters/{mid}/drafts", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def save(
    client: AsyncClient, did: str, headers: dict[str, str], content: str, base: int, **kw: Any
) -> Any:
    return await client.post(
        f"/v1/drafts/{did}/versions",
        json={"content": content, "base_version_no": base, **kw},
        headers=headers,
    )


async def get(client: AsyncClient, did: str, headers: dict[str, str]) -> dict[str, Any]:
    r = await client.get(f"/v1/drafts/{did}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode()


async def test_every_save_is_a_version_and_history_can_be_compared_and_restored(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    h = world.associate_a.headers
    d = await create(
        client, mid, h, title="Notice to Zenith", kind="notice", content="Take notice that payment is due."
    )
    assert d["current_version_no"] == 1 and d["status"] == "draft" and d["can_edit"] is True

    r = await save(
        client,
        d["id"],
        h,
        "Take notice that payment of Rs. 5,00,000 is due within 15 days.",
        1,
        note="Added amount",
    )
    assert r.status_code == 201 and r.json()["version_no"] == 2
    assert (
        await save(client, d["id"], h, "Take notice that payment of Rs. 5,00,000 is due within 15 days.", 2)
    ).status_code == 422

    full = await get(client, d["id"], h)
    assert [v["version_no"] for v in full["versions"]] == [2, 1] and full["versions"][0][
        "note"
    ] == "Added amount"
    assert "5,00,000" in full["content"]

    diff = (await client.get(f"/v1/drafts/{d['id']}/diff", params={"from": 1, "to": 2}, headers=h)).json()
    assert diff["inserted_words"] >= 5 and diff["deleted_words"] == 0
    assert any(op["op"] == "insert" and "5,00,000" in op["text"] for op in diff["ops"])

    v1 = (await client.get(f"/v1/drafts/{d['id']}/versions/1", headers=h)).json()
    assert v1["content"] == "Take notice that payment is due."
    r = await client.post(f"/v1/drafts/{d['id']}/versions/1/restore", headers=h)
    assert (
        r.status_code == 201 and r.json()["version_no"] == 3 and r.json()["note"] == "Restored from version 1"
    )
    full = await get(client, d["id"], h)
    assert full["content"] == v1["content"] and len(full["versions"]) == 3  # nothing was overwritten
    assert {"draft.create", "draft.save", "draft.restore"} <= {a["action"] for a in full["activity"]}


async def test_simultaneous_edits_conflict_instead_of_overwriting(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.partner_a.headers, content="Draft one.")
    # Both open version 1. The associate saves first.
    assert (
        await save(client, d["id"], world.associate_a.headers, "Draft one, associate edit.", 1)
    ).status_code == 201
    late = await save(client, d["id"], world.partner_a.headers, "Draft one, partner edit.", 1)
    assert late.status_code == 409 and "saved version 2" in late.json()["detail"]
    on_top = await save(client, d["id"], world.partner_a.headers, "Draft one, partner edit.", 1, force=True)
    assert on_top.status_code == 201 and on_top.json()["version_no"] == 3
    versions = (await get(client, d["id"], world.partner_a.headers))["versions"]
    assert [v["created_by"]["id"] for v in versions[:2]] == [
        str(world.partner_a.id),
        str(world.associate_a.id),
    ]


async def test_viewers_read_and_comment_but_cannot_edit(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, content="Para 1. Para 2.")
    viewer = world.paralegal_a.headers
    full = await get(client, d["id"], viewer)
    assert full["can_edit"] is False and full["my_matter_role"] == "viewer"
    assert (await save(client, d["id"], viewer, "changed", 1)).status_code == 403
    assert (await client.post(f"/v1/matters/{mid}/drafts", json={}, headers=viewer)).status_code == 403
    r = await client.post(
        f"/v1/drafts/{d['id']}/comments",
        json={"body": "Cite the agreement here", "quote": "Para 2."},
        headers=viewer,
    )
    assert r.status_code == 201
    comment = r.json()["comments"][0]
    assert comment["quote"] == "Para 2." and comment["author"]["id"] == str(world.paralegal_a.id)
    r = await client.post(
        f"/v1/drafts/{d['id']}/comments/{comment['id']}/resolve", headers=world.associate_a.headers
    )
    assert r.json()["comments"][0]["resolved_by"]["id"] == str(world.associate_a.id)


async def test_approval_flow_locks_records_and_survives_later_edits(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    author, approver = world.associate_a.headers, world.partner_a.headers
    d = await create(
        client, mid, author, title="Reply to notice", kind="reply", content="Our client denies liability."
    )
    did = d["id"]

    r = await client.post(
        f"/v1/drafts/{did}/review",
        json={"reviewer_id": str(world.partner_a.id), "message": "Please check"},
        headers=author,
    )
    assert r.status_code == 200 and r.json()["status"] == "in_review" and r.json()["can_edit"] is False
    assert (await save(client, did, author, "edited during review", 1)).status_code == 409
    assert (
        await client.post(
            f"/v1/drafts/{did}/review", json={"reviewer_id": str(world.partner_a.id)}, headers=author
        )
    ).status_code == 409
    waiting = (await client.get("/v1/drafts/awaiting-me", headers=approver)).json()
    assert did in [x["id"] for x in waiting] and waiting[0]["reviewer"]["id"] == str(world.partner_a.id)

    bad = await client.post(
        f"/v1/drafts/{did}/review/decision", json={"decision": "request_changes"}, headers=approver
    )
    assert bad.status_code == 422
    r = await client.post(
        f"/v1/drafts/{did}/review/decision",
        json={"decision": "request_changes", "note": "Add the 15-day deadline"},
        headers=approver,
    )
    assert (
        r.json()["status"] == "changes_requested"
        and r.json()["reviews"][0]["decision_note"] == "Add the 15-day deadline"
    )

    assert (
        await save(client, did, author, "Our client denies liability and will respond within 15 days.", 1)
    ).status_code == 201
    await client.post(
        f"/v1/drafts/{did}/review", json={"reviewer_id": str(world.partner_a.id)}, headers=author
    )
    r = await client.post(f"/v1/drafts/{did}/review/decision", json={"decision": "approve"}, headers=approver)
    approved = r.json()
    assert approved["status"] == "approved" and approved["approved_version_no"] == 2
    assert approved["approved_by"]["id"] == str(world.partner_a.id)
    assert (
        await client.post(
            f"/v1/drafts/{did}/review", json={"reviewer_id": str(world.partner_a.id)}, headers=author
        )
    ).status_code == 409

    assert (await save(client, did, author, "Our client denies all liability.", 2)).status_code == 201
    after = await get(client, did, author)
    assert (
        after["status"] == "draft" and after["current_version_no"] == 3 and after["approved_version_no"] == 2
    )

    v2 = await client.get(f"/v1/drafts/{did}/export", params={"version": 2}, headers=author)
    assert v2.status_code == 200 and "approved by" in docx_text(v2.content)
    v3 = await client.get(f"/v1/drafts/{did}/export", headers=author)
    assert "NOT APPROVED (version 2 is the approved version)" in docx_text(v3.content)
    actions = {a["action"] for a in (await get(client, did, author))["activity"]}
    assert {
        "draft.review_request",
        "draft.changes_requested",
        "draft.approve",
        "draft.save",
        "draft.export",
    } <= actions


async def test_who_may_approve(client: AsyncClient, world: World, new_matter: dict[str, Any]) -> None:
    mid = await team(client, world, new_matter)
    author = world.associate_a.headers
    d = await create(client, mid, author, content="x y z")
    for reviewer in (
        world.associate_a,
        world.paralegal_a,
        world.admin_a,
        world.partner_b,
    ):  # self, viewer, non-member, other firm
        r = await client.post(
            f"/v1/drafts/{d['id']}/review", json={"reviewer_id": str(reviewer.id)}, headers=author
        )
        assert r.status_code == 422, (reviewer.email, r.text)
    # The lead may not approve a review they requested themselves
    own = await create(client, mid, world.partner_a.headers, content="lead's own draft")
    r = await client.post(
        f"/v1/drafts/{own['id']}/review",
        json={"reviewer_id": str(world.partner_a.id)},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 422

    await client.post(
        f"/v1/drafts/{d['id']}/review", json={"reviewer_id": str(world.partner_a.id)}, headers=author
    )
    r = await client.post(
        f"/v1/drafts/{d['id']}/review/decision", json={"decision": "approve"}, headers=author
    )
    assert r.status_code == 403  # only the chosen approver decides
    assert (
        await client.post(f"/v1/drafts/{d['id']}/review/withdraw", headers=world.paralegal_a.headers)
    ).status_code == 403
    r = await client.post(f"/v1/drafts/{d['id']}/review/withdraw", headers=author)
    assert r.status_code == 200 and r.json()["status"] == "draft"


async def test_drafts_inherit_matter_permissions(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, content="confidential")
    for outsider in (
        world.admin_a,
        world.partner_b,
    ):  # admins get no implicit access; other firm sees nothing
        assert (await client.get(f"/v1/drafts/{d['id']}", headers=outsider.headers)).status_code == 404
        assert (await client.get(f"/v1/matters/{mid}/drafts", headers=outsider.headers)).status_code == 404
    await client.post(
        f"/v1/drafts/{d['id']}/review",
        json={"reviewer_id": str(world.partner_a.id)},
        headers=world.associate_a.headers,
    )
    r = await client.post(
        f"/v1/matters/{mid}/walls",
        json={"user_id": str(world.partner_a.id), "reason": "Conflict found late"},
        headers=world.admin_a.headers,
    )
    assert r.status_code == 201
    assert (await client.get(f"/v1/drafts/{d['id']}", headers=world.partner_a.headers)).status_code == 404
    waiting = (await client.get("/v1/drafts/awaiting-me", headers=world.partner_a.headers)).json()
    assert d["id"] not in [x["id"] for x in waiting]


async def test_draft_from_an_assistant_answer_keeps_citations(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await matter_with_documents(client, world, new_matter, run_jobs)
    h = world.partner_a.headers
    tid = await new_thread(client, mid, h)
    ev = await ask(
        client, tid, h, content="Reply refusing the payment demand", mode="draft", draft_kind="reply"
    )
    answer = ev[-1][1]
    d = await create(client, mid, h, from_message_id=answer["id"])
    assert d["kind"] == "reply" and d["title"].startswith("Reply to notice:")
    assert "[1]" in d["content"] and d["sources"][0]["n"] == 1 and d["sources"][0]["chunk_id"]
    assert d["versions"][0]["note"] == "Created from an Assistant answer"

    other = await client.post("/v1/matters", json={"title": "Another matter", "client_name": "X"}, headers=h)
    r = await client.post(
        f"/v1/matters/{other.json()['id']}/drafts", json={"from_message_id": answer["id"]}, headers=h
    )
    assert r.status_code == 404


async def test_presence_shows_who_else_is_editing(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, content="text")
    r = await client.post(
        f"/v1/drafts/{d['id']}/presence", json={"editing": True}, headers=world.associate_a.headers
    )
    assert r.status_code == 200 and r.json()["present"] == []  # never lists yourself
    head = (
        await client.post(f"/v1/drafts/{d['id']}/presence", json={}, headers=world.paralegal_a.headers)
    ).json()
    assert [(x["user"]["id"], x["editing"]) for x in head["present"]] == [(str(world.associate_a.id), True)]
    assert head["current_version_no"] == 1
    head = (
        await client.post(
            f"/v1/drafts/{d['id']}/presence", json={"editing": True}, headers=world.associate_a.headers
        )
    ).json()
    assert [(x["user"]["id"], x["editing"]) for x in head["present"]] == [(str(world.paralegal_a.id), False)]
