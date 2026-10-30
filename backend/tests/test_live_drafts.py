"""DRF-08..11 (Step 2c), ADR-020: several people edit one draft at once, AI suggestions as redlines,
approval of exactly what everyone sees — over a real WebSocket, with Python Yjs clients standing in
for browsers (same protocol as y-websocket)."""

import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from httpx_ws import WebSocketDisconnect, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from pycrdt import Decoder, Doc, Text, create_sync_message, create_update_message, handle_sync_message

from app.core.llm import Usage, use_backend
from app.main import create_app
from app.modules.drafts import live
from tests.conftest import SeedUser, World
from tests.test_drafts_api import create, get, team
from tests.test_vault_api import RunJobs, upload

SPA = (
    b"SHARE PURCHASE AGREEMENT\n\n12. TERMINATION\n"
    b"12.1 Either party may terminate this Agreement by giving thirty days written notice to the other party.\n"
)
NOTICE = "Take notice that my client demands payment within 15 days.\n\nयह नोटिस अंतिम है।"


def token(u: SeedUser) -> str:
    return u.headers["Authorization"].removeprefix("Bearer ")


class YClient:
    """A browser stand-in: a Yjs document kept in sync with the room over the socket."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.doc: Any = Doc()
        self.text: Any = self.doc.get("content", type=Text)
        self.events: list[dict[str, Any]] = []
        self.closed: int | None = None

    def value(self) -> str:
        return str(self.text)

    async def pump(self, seconds: float = 0.25) -> None:
        """Handle everything the server sends until it goes quiet."""
        while self.closed is None:
            try:
                data = await self.ws.receive_bytes(timeout=seconds)
            except TimeoutError:
                return
            except WebSocketDisconnect as e:
                self.closed = e.code
                return
            if data[0] == 0:
                reply = handle_sync_message(data[1:], self.doc)
                if reply:
                    await self.ws.send_bytes(reply)
            elif data[0] == live.MSG_APP:
                self.events.append(json.loads(Decoder(data[1:]).read_var_string()))

    async def insert(self, at: int, s: str) -> None:
        before = self.doc.get_state()
        self.text.insert(len(self.value()[:at].encode()), s)
        with contextlib.suppress(Exception):  # the server may already have closed the socket
            await self.ws.send_bytes(create_update_message(self.doc.get_update(before)))

    def event_types(self) -> list[str]:
        return [e["type"] for e in self.events]


@asynccontextmanager
async def browser(
    draft_id: str, user: SeedUser | None, protocols: list[str] | None = None
) -> AsyncIterator[YClient]:
    transport = ASGIWebSocketTransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:  # enters the transport too
        subprotocols = protocols if protocols is not None else ["dataviv", token(user)] if user else []
        async with aconnect_ws(f"/v1/drafts/{draft_id}/live", c, subprotocols=subprotocols) as ws:
            y = YClient(ws)
            await ws.send_bytes(create_sync_message(y.doc))
            await y.pump()
            yield y


async def live_text(client: AsyncClient, did: str, user: SeedUser) -> dict[str, Any]:
    r = await client.get(f"/v1/drafts/{did}/live/text", headers=user.headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
async def fresh_rooms() -> AsyncIterator[None]:
    await live.reset()
    yield
    await live.reset()


# --------------------------------------------------------------------------- co-typing


async def test_two_people_type_at_once_and_save_one_credited_version(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Notice", kind="notice", content=NOTICE)
    did = d["id"]
    async with browser(did, world.associate_a) as rohan, browser(did, world.partner_a) as asha:
        assert rohan.value() == NOTICE == asha.value()  # both start from version 1
        assert rohan.events[0] == {"type": "hello", "can_edit": True, "locked": False}
        await rohan.insert(0, "WITHOUT PREJUDICE\n")
        await asha.insert(len(asha.value()), "\nRegards, Asha Sharma")  # at the same time, other end
        for _ in range(3):
            await rohan.pump()
            await asha.pump()
        assert rohan.value() == asha.value()
        merged = rohan.value()
        assert merged.startswith("WITHOUT PREJUDICE\nTake notice") and merged.endswith("Regards, Asha Sharma")
        assert "यह नोटिस अंतिम है।" in merged

        lt = await live_text(client, did, world.paralegal_a)  # viewers read the working copy too
        assert lt["text"] == merged and lt["unsaved_changes"] is True and lt["current_version_no"] == 1
        detail = await get(client, did, world.associate_a.headers)
        assert detail["live"]["available"] and detail["live"]["unsaved_changes"]
        assert {p["name"] for p in detail["live"]["contributors"]} == {"associate", "partner"}

        r = await client.post(
            f"/v1/drafts/{did}/live/save", json={"note": "Joint edit"}, headers=world.associate_a.headers
        )
        assert r.status_code == 201, r.text
        v = r.json()
        assert v["version_no"] == 2 and {p["name"] for p in v["contributors"]} == {"associate", "partner"}
        await rohan.pump()
        assert {"type": "changed", "what": "version"}.items() <= rohan.events[-1].items()
        assert (
            await client.post(f"/v1/drafts/{did}/live/save", json={}, headers=world.associate_a.headers)
        ).status_code == 422
    detail = await get(client, did, world.associate_a.headers)
    assert detail["live"]["unsaved_changes"] is False and detail["live"]["contributors"] == []


async def test_the_working_copy_survives_a_restart(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Memo", content=NOTICE)
    async with browser(d["id"], world.associate_a) as rohan:
        await rohan.insert(0, "DRAFT: ")
        await rohan.pump()
    await live.reset()  # as if the API restarted: rooms are rebuilt from the saved Yjs state
    async with browser(d["id"], world.partner_a) as asha:
        assert asha.value() == "DRAFT: " + NOTICE


# --------------------------------------------------------------------------- who may connect and edit


async def test_viewers_read_live_but_cannot_change_it(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Memo", content=NOTICE)
    async with browser(d["id"], world.associate_a) as rohan, browser(d["id"], world.paralegal_a) as viewer:
        assert viewer.events[0]["can_edit"] is False and viewer.value() == NOTICE
        await rohan.insert(0, "A")
        await rohan.pump()
        await viewer.pump()
        assert viewer.value() == "A" + NOTICE  # sees live typing
        await viewer.insert(0, "HACK ")
        await viewer.pump()
        assert viewer.closed == live.CLOSE_VIEWER
        await rohan.pump()
        assert rohan.value() == "A" + NOTICE
    assert (await live_text(client, d["id"], world.partner_a))["text"] == "A" + NOTICE


async def test_strangers_are_turned_away(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Memo", content=NOTICE)
    for user, protocols, code in (
        (world.partner_b, None, live.CLOSE_NOT_FOUND),  # other firm
        (world.admin_a, None, live.CLOSE_NOT_FOUND),  # admins get no implicit access
        (None, ["dataviv", "not-a-token"], live.CLOSE_UNAUTHORISED),
        (None, [], live.CLOSE_UNAUTHORISED),  # no token at all
    ):
        async with browser(d["id"], user, protocols) as y:
            assert y.closed == code and y.value() == ""
    async with browser(str(uuid.uuid4()), world.partner_a) as y:
        assert y.closed == live.CLOSE_NOT_FOUND


async def test_a_wall_disconnects_someone_already_editing(
    client: AsyncClient, world: World, new_matter: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live, "RECHECK_SECONDS", 0.2)
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Memo", content=NOTICE)
    async with browser(d["id"], world.associate_a) as rohan:
        r = await client.post(
            f"/v1/matters/{mid}/walls",
            json={"user_id": str(world.associate_a.id), "reason": "Conflict found"},
            headers=world.admin_a.headers,
        )
        assert r.status_code == 201, r.text
        await rohan.pump(seconds=0.6)
        assert rohan.closed == live.CLOSE_NOT_FOUND


# --------------------------------------------------------------------------- approval of what everyone sees


async def test_approval_freezes_exactly_what_everyone_sees(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Notice", content=NOTICE)
    did = d["id"]
    async with browser(did, world.associate_a) as rohan, browser(did, world.partner_a) as asha:
        await rohan.insert(0, "FINAL: ")
        await rohan.pump()
        r = await client.post(
            f"/v1/drafts/{did}/review",
            json={"reviewer_id": str(world.partner_a.id), "message": "Please approve"},
            headers=world.associate_a.headers,
        )
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail["status"] == "in_review" and detail["current_version_no"] == 2
        assert detail["versions"][0]["note"] == "Saved for approval"
        assert [p["name"] for p in detail["versions"][0]["contributors"]] == ["associate"]
        await asha.pump()
        assert {"type": "status", "status": "in_review", "locked": True} in asha.events

        await asha.insert(0, "x")  # even the approver cannot type into a locked draft
        await asha.pump()
        assert asha.closed == live.CLOSE_LOCKED
        assert (await live_text(client, did, world.associate_a))["text"] == "FINAL: " + NOTICE
        r = await client.post(
            f"/v1/drafts/{did}/suggestions",
            json={"instruction": "Shorten it"},
            headers=world.associate_a.headers,
        )
        assert r.status_code == 409  # no AI edits while waiting for approval either

        r = await client.post(
            f"/v1/drafts/{did}/review/decision", json={"decision": "approve"}, headers=world.partner_a.headers
        )
        assert r.status_code == 200 and r.json()["approved_version_no"] == 2
        await rohan.pump()
        assert {"type": "status", "status": "approved", "locked": False} in rohan.events
        await rohan.insert(0, "v3 ")  # editing again after approval; v2 stays the approved version
        await rohan.pump()
        assert rohan.closed is None
    detail = await get(client, did, world.partner_a.headers)
    assert detail["approved_version_no"] == 2 and detail["live"]["unsaved_changes"] is True


async def test_versions_saved_or_restored_outside_the_editor_reach_everyone(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    d = await create(client, mid, world.associate_a.headers, title="Memo", content="One.")
    async with browser(d["id"], world.partner_a) as asha:
        r = await client.post(
            f"/v1/drafts/{d['id']}/versions",
            json={"content": "Two.", "base_version_no": 1},
            headers=world.associate_a.headers,
        )
        assert r.status_code == 201
        await asha.pump()
        assert asha.value() == "Two."
        r = await client.post(f"/v1/drafts/{d['id']}/versions/1/restore", headers=world.associate_a.headers)
        assert r.status_code == 201
        await asha.pump()
        assert asha.value() == "One."


# --------------------------------------------------------------------------- AI suggestions


async def test_ai_suggestion_is_a_shared_redline_until_someone_accepts_it(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    mid = await team(client, world, new_matter)
    await upload(client, mid, world.partner_a.headers, ("spa.txt", SPA))
    await run_jobs()
    text = "Notice under the agreement.\n\nThe Buyer may end the agreement on notice.\n\nयह नोटिस अंतिम है।"
    d = await create(client, mid, world.associate_a.headers, title="Reply", kind="reply", content=text)
    did = d["id"]
    clause = "The Buyer may end the agreement on notice."
    async with browser(did, world.associate_a) as rohan, browser(did, world.partner_a) as asha:
        r = await client.post(
            f"/v1/drafts/{did}/suggestions",
            json={
                "instruction": "State the termination notice period and cite the SPA",
                "selection": {"text": clause, "start": text.index(clause)},
            },
            headers=world.associate_a.headers,
        )
        assert r.status_code == 201, r.text
        s = r.json()
        assert s["status"] == "pending" and s["scope"] == "selection" and s["quote"] == clause
        assert s["replacement"] == clause + " [revised]"
        assert s["sources"] and s["sources"][0]["document_title"] == "spa.txt"  # grounded in the matter
        assert any(op["op"] == "insert" for op in s["diff"]) and s["model"]
        await asha.pump()
        assert {"type": "changed", "what": "suggestion"}.items() <= asha.events[-1].items()
        assert asha.value() == text  # nothing changes until a person accepts

        detail = await get(client, did, world.partner_a.headers)
        assert [x["id"] for x in detail["suggestions"]] == [s["id"]]

        r = await client.post(
            f"/v1/drafts/{did}/suggestions/{s['id']}/accept", headers=world.partner_a.headers
        )
        assert r.status_code == 200 and r.json()["status"] == "accepted"
        await rohan.pump()
        await asha.pump()
        expected = text.replace(clause, clause + " [revised]")
        assert rohan.value() == expected == asha.value()  # Hindi after the edit is intact
        detail = await get(client, did, world.associate_a.headers)
        assert {p["name"] for p in detail["live"]["contributors"]} == {"associate", "partner"}
        again = await client.post(
            f"/v1/drafts/{did}/suggestions/{s['id']}/accept", headers=world.partner_a.headers
        )
        assert again.status_code == 409


async def test_suggestions_go_stale_or_are_rejected_and_viewers_cannot_ask(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    text = "Clause one is short.\n\nClause two is long and winding."
    d = await create(client, mid, world.associate_a.headers, title="Memo", content=text)
    did, h = d["id"], world.associate_a.headers

    async def ask(selected: str) -> dict[str, Any]:
        r = await client.post(
            f"/v1/drafts/{did}/suggestions",
            json={
                "instruction": "Make it formal",
                "selection": {"text": selected, "start": text.index(selected)},
                "use_documents": False,
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    first, second = await ask("Clause one is short."), await ask("Clause two is long and winding.")
    async with browser(did, world.partner_a) as asha:
        await asha.insert(asha.value().index("short"), "very ")  # someone edits the quoted text meanwhile
        await asha.pump()
        r = await client.post(f"/v1/drafts/{did}/suggestions/{first['id']}/accept", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "stale"
        assert "Clause one is very short." in asha.value()  # untouched
    r = await client.post(
        f"/v1/drafts/{did}/suggestions/{second['id']}/reject", headers=world.partner_a.headers
    )
    assert (
        r.status_code == 200
        and r.json()["status"] == "rejected"
        and r.json()["decided_by"]["name"] == "partner"
    )

    r = await client.post(
        f"/v1/drafts/{did}/suggestions",
        json={"instruction": "Make it formal"},
        headers=world.paralegal_a.headers,
    )
    assert r.status_code == 403
    r = await client.post(
        f"/v1/drafts/{did}/suggestions",
        json={"instruction": "Make it formal", "selection": {"text": "Not in the draft", "start": 0}},
        headers=h,
    )
    assert r.status_code == 409
    r = await client.post(f"/v1/drafts/{did}/suggestions", json={"instruction": "x"}, headers=h)
    assert r.status_code == 422
    r = await client.post(
        f"/v1/drafts/{did}/suggestions", json={"instruction": "Tidy"}, headers=world.partner_b.headers
    )
    assert r.status_code == 404


class InventingModel:
    """Answers with a figure that is nowhere in the draft or the documents."""

    name = "inventing"

    def models(self) -> list[str]:
        return ["inventing-1"]

    async def complete(
        self, model: str, messages: Any, *, max_tokens: int, json_schema: Any
    ) -> tuple[str, Usage]:
        body = {
            "replacement": "Pay Rs. 9,99,999 within 7 days.",
            "explanation": "Added terms.",
            "cited": ["S9"],
        }
        return json.dumps(body), Usage(100, 20)

    async def stream(self, model: str, messages: Any, *, max_tokens: int) -> Any:  # pragma: no cover
        raise NotImplementedError


async def test_figures_the_model_made_up_are_flagged(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    text = "Pay the dues within 15 days."
    d = await create(client, mid, world.associate_a.headers, title="Memo", content=text)
    with use_backend(InventingModel()):
        r = await client.post(
            f"/v1/drafts/{d['id']}/suggestions",
            json={"instruction": "Add the amount", "use_documents": False},
            headers=world.associate_a.headers,
        )
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["scope"] == "draft" and s["quote"] == text
    warnings = " ".join(s["warnings"])
    assert "999999" in warnings and "7" in warnings and "not in the original text" in warnings
    assert "cited passages it was not given: S9" in warnings


def test_clause_numbers_survive_an_ai_rewrite() -> None:
    from app.modules.drafts.suggest import keep_marker

    assert (
        keep_marker("3. Our client may end it.", "The Seller must cure it.") == "3. The Seller must cure it."
    )
    assert keep_marker("12.1 Either party may.", "12.1 Either party shall.") == "12.1 Either party shall."
    assert keep_marker("(a) the shares;", "the Sale Shares;") == "(a) the Sale Shares;"
    assert keep_marker("3. Old text.", "4. New text.") == "3. New text."  # the draft's numbering wins
    assert keep_marker("Plain sentence.", "Better sentence.") == "Better sentence."
