"""Drafting studio add-on: templates, streamed generation and revision, firm-wide history, edits,
permissions, tenant isolation, audit and Word export — end to end against PostgreSQL, offline model."""

import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.llm import LLMError, Message, Notice, TextDelta, Usage, use_backend
from app.modules.studio.service import settings as studio_settings
from tests.conftest import World, _user

NDA_TEXT = """**MUTUAL NON-DISCLOSURE AGREEMENT**

This Agreement is made at Mumbai on [date of execution] between Acme Pvt Ltd and Beta LLP.

# 1. DEFINITIONS
1.1 "Confidential Information" means all information disclosed by either party.
(a) technical data;

2. GOVERNING LAW
2.1 This Agreement is governed by the laws of India; courts at Mumbai have jurisdiction.

DRAFTING NOTES
- Fill in the date of execution.
- Execute on stamp paper of the value required in Maharashtra."""


class ScriptedBackend:
    """Streams a fixed document and records what the studio sent."""

    name = "scripted"

    def __init__(self, reply: str = NDA_TEXT) -> None:
        self.reply = reply
        self.calls: list[tuple[list[Message], int]] = []

    def models(self) -> list[str]:
        return ["fake-1"]

    async def complete(
        self, model: str, messages: list[Message], *, max_tokens: int, json_schema: Any
    ) -> tuple[str, Usage]:
        raise LLMError("unused")

    async def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]:
        self.calls.append((messages, max_tokens))
        for i in range(0, len(self.reply), 40):
            yield TextDelta(self.reply[i : i + 40])
        yield Usage(900, 450)


class FailingBackend(ScriptedBackend):
    async def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]:
        yield Notice("Waiting for the model server.")
        yield TextDelta("NOTICE\n\n1. Under instructions")
        raise LLMError("Model server unreachable", status=503)


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def stream(
    client: AsyncClient, path: str, headers: dict[str, str], **body: Any
) -> list[tuple[str, dict[str, Any]]]:
    r = await client.post(path, json=body, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return events(r.text)


def nda(tag: str) -> dict[str, Any]:
    return {
        "template": "nda",
        "title": f"NDA Acme Beta {tag}",
        "inputs": {
            "nda_type": "Mutual",
            "party_a": "Acme Pvt Ltd, Andheri, Mumbai",
            "party_b": "Beta LLP, Pune",
            "purpose": "Evaluating a distribution arrangement",
            "jurisdiction": "Laws of India; courts at Mumbai",
            "term": "   ",  # blank answers are dropped
        },
        "instructions": "Keep it to two pages.",
    }


async def generate(client: AsyncClient, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    ev = await stream(client, "/v1/studio/documents", headers, **body)
    assert ev[-1][0] == "done", ev[-1]
    return ev[-1][1]


async def test_templates_cover_contracts_notices_and_more(client: AsyncClient, world: World) -> None:
    r = await client.get("/v1/studio/templates", headers=world.associate_a.headers)
    assert r.status_code == 200
    by_key = {t["key"]: t for t in r.json()}
    assert {"nda", "service_agreement", "notice_138", "notice_reply", "affidavit", "letter", "custom"} <= set(
        by_key
    )
    assert {t["category"] for t in r.json()} == {
        "Contracts",
        "Notices",
        "Affidavits and authorities",
        "Advice and letters",
        "Any other document",
    }
    cheque = {f["key"]: f for f in by_key["notice_138"]["fields"]}
    assert cheque["cheque"]["required"] and cheque["liability"]["long"]
    assert "brief" not in by_key["nda"]  # drafting guidance stays on the server


async def test_generate_streams_cleans_and_stores_a_document(
    client: AsyncClient, world: World, owner_engine: AsyncEngine
) -> None:
    backend = ScriptedBackend()
    with use_backend(backend):
        ev = await stream(client, "/v1/studio/documents", world.associate_a.headers, **nda("gen"))
    kinds = [k for k, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done" and "delta" in kinds
    meta, doc = ev[0][1], ev[-1][1]
    assert meta["document_id"] == doc["id"] == doc["family_id"] and meta["parent_id"] is None
    assert doc["status"] == "complete" and doc["template_name"] == "Non-disclosure agreement"
    assert doc["category"] == "Contracts" and doc["created_by"]["name"] == "associate"
    assert doc["output_tokens"] == 450 and doc["versions"] == 1
    # Markdown the model added anyway is stripped; the notes stay in the text for the lawyer.
    assert doc["content"].startswith("MUTUAL NON-DISCLOSURE AGREEMENT\n")
    assert "**" not in doc["content"] and "# 1." not in doc["content"]
    assert "DRAFTING NOTES" in doc["content"]
    assert doc["inputs"] == {k: v for k, v in nda("gen")["inputs"].items() if v.strip()}

    messages, max_tokens = backend.calls[0]
    assert max_tokens == studio_settings.max_output_tokens
    system, user = messages[0].content, messages[1].content
    assert "square brackets" in system and "Bharatiya Nyaya Sanhita, 2023" in system
    assert "<source" not in user  # never grounded in matter documents
    assert "Second party: Beta LLP, Pune" in user and "Keep it to two pages." in user and "Term:" not in user

    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT action, detail::text FROM audit_events WHERE object_id = :d ORDER BY id"),
                {"d": doc["id"]},
            )
        ).all()
    actions = [a for a, _ in rows]
    assert actions == ["studio.generate", "ai.call"]
    call = json.loads(rows[1][1])
    assert call["feature"] == "studio" and call["template"] == "nda" and len(call["prompt_sha256"]) == 64


async def test_validation_errors_come_back_before_streaming(client: AsyncClient, world: World) -> None:
    h = world.partner_a.headers
    r = await client.post("/v1/studio/documents", json={"template": "sale_of_moon"}, headers=h)
    assert r.status_code == 422 and "Unknown template" in r.text
    r = await client.post(
        "/v1/studio/documents", json={"template": "nda", "inputs": {"colour": "blue"}}, headers=h
    )
    assert r.status_code == 422 and "colour" in r.text
    r = await client.post("/v1/studio/documents", json={"template": "affidavit"}, headers=h)
    assert r.status_code == 422 and "Deponent" in r.text
    r = await client.post(
        "/v1/studio/documents", json={"template": "nda", "inputs": {"purpose": "x" * 7000}}, headers=h
    )
    assert r.status_code == 422
    # Missing facts are fine when the instructions describe the document: the draft uses placeholders.
    doc = await generate(
        client, h, {"template": "affidavit", "instructions": "Affidavit of name change for a passport"}
    )
    assert doc["status"] == "complete" and doc["title"] == "Affidavit"


async def test_clients_cannot_use_the_studio(
    client: AsyncClient, world: World, owner_engine: AsyncEngine
) -> None:
    c = await _user(owner_engine, world.firm_a, "client", f"client-{uuid.uuid4().hex[:6]}@acme.in")
    assert (await client.get("/v1/studio/templates", headers=c.headers)).status_code == 403
    r = await client.post("/v1/studio/documents", json=nda("client"), headers=c.headers)
    assert r.status_code == 403
    assert (await client.get("/v1/studio/documents", headers=c.headers)).status_code == 403


async def test_revise_keeps_versions_and_history_shows_latest(client: AsyncClient, world: World) -> None:
    tag = uuid.uuid4().hex[:8]
    with use_backend(ScriptedBackend()):
        first = await generate(client, world.associate_a.headers, nda(tag))
    backend = ScriptedBackend(NDA_TEXT.replace("Mumbai on", "Pune on"))
    with use_backend(backend):
        ev = await stream(
            client,
            f"/v1/studio/documents/{first['id']}/revise",
            world.partner_a.headers,
            instructions="Make Pune the place of execution.",
        )
    second = ev[-1][1]
    assert ev[0][1]["parent_id"] == first["id"] and second["family_id"] == first["id"]
    assert second["parent_id"] == first["id"] and "Pune on" in second["content"]
    assert [r["id"] for r in second["revisions"]] == [first["id"], second["id"]]
    assert second["revisions"][1]["instructions"] == "Make Pune the place of execution."
    roles = [m.role for m in backend.calls[0][0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert backend.calls[0][0][1].content.count("Keep it to two pages.") == 1  # the original request
    assert backend.calls[0][0][2].content == first["content"]

    r = await client.get(f"/v1/studio/documents?q={tag}", headers=world.associate_a.headers)
    rows = r.json()
    assert [d["id"] for d in rows] == [second["id"]] and rows[0]["versions"] == 2
    r = await client.get(
        "/v1/studio/documents?q=distribution%20arrangement&template=nda", headers=world.associate_a.headers
    )
    assert second["id"] in [d["id"] for d in r.json()]
    r = await client.get(f"/v1/studio/documents?q={tag}&template=letter", headers=world.associate_a.headers)
    assert r.json() == []
    r = await client.get(f"/v1/studio/documents?q={tag}&mine=true", headers=world.paralegal_a.headers)
    assert r.json() == []
    # Older versions stay readable.
    old = (await client.get(f"/v1/studio/documents/{first['id']}", headers=world.paralegal_a.headers)).json()
    assert "Mumbai on" in old["content"] and old["versions"] == 2


async def test_other_firms_never_see_studio_documents(client: AsyncClient, world: World) -> None:
    tag = uuid.uuid4().hex[:8]
    with use_backend(ScriptedBackend()):
        doc = await generate(client, world.partner_a.headers, nda(tag))
    h = world.partner_b.headers
    assert (await client.get(f"/v1/studio/documents/{doc['id']}", headers=h)).status_code == 404
    assert (await client.get(f"/v1/studio/documents?q={tag}", headers=h)).json() == []
    assert (await client.get(f"/v1/studio/documents/{doc['id']}/export", headers=h)).status_code == 404
    r = await client.post(f"/v1/studio/documents/{doc['id']}/revise", json={"instructions": "x"}, headers=h)
    assert r.status_code == 404


async def test_edit_export_and_delete(client: AsyncClient, world: World, owner_engine: AsyncEngine) -> None:
    with use_backend(ScriptedBackend()):
        doc = await generate(client, world.associate_a.headers, nda(uuid.uuid4().hex[:8]))
    url = f"/v1/studio/documents/{doc['id']}"
    r = await client.patch(url, json={"title": "Not mine"}, headers=world.paralegal_a.headers)
    assert r.status_code == 403
    edited = doc["content"].replace("[date of execution]", "1 December 2026")
    r = await client.patch(
        url, json={"title": "NDA (final)", "content": edited}, headers=world.associate_a.headers
    )
    assert (
        r.status_code == 200
        and r.json()["title"] == "NDA (final)"
        and "1 December 2026" in r.json()["content"]
    )

    r = await client.get(f"{url}/export", headers=world.paralegal_a.headers)
    assert r.status_code == 200
    assert "NDA%20_final_.docx" in r.headers["content-disposition"]
    xml = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()
    assert "MUTUAL NON-DISCLOSURE AGREEMENT" in xml and '<w:jc w:val="center"/>' in xml
    assert "Drafting notes (remove before sending)" in xml and "Fill in the date of execution." in xml
    assert "DRAFTING NOTES" not in xml and "Prepared with Dataviv AI OS" in xml
    assert '<w:ind w:left="567"/>' in xml  # sub-clauses indented

    r = await client.delete(url, headers=world.paralegal_a.headers)
    assert r.status_code == 403
    r = await client.delete(url, headers=world.partner_a.headers)
    assert r.status_code == 204
    assert (await client.get(url, headers=world.partner_a.headers)).status_code == 404
    listed = (await client.get("/v1/studio/documents?limit=200", headers=world.partner_a.headers)).json()
    assert doc["id"] not in [d["id"] for d in listed]
    async with owner_engine.connect() as conn:
        actions = (
            await conn.scalars(
                text("SELECT action FROM audit_events WHERE object_id = :d ORDER BY id"), {"d": doc["id"]}
            )
        ).all()
    assert actions[-3:] == ["studio.update", "studio.export", "studio.delete"]


async def test_model_failure_is_stored_and_reported(client: AsyncClient, world: World) -> None:
    with use_backend(FailingBackend()):
        ev = await stream(client, "/v1/studio/documents", world.partner_a.headers, **nda("fail"))
    assert [k for k, _ in ev] == ["meta", "notice", "delta", "error"]
    err = ev[-1][1]
    assert err["status"] == 503 and err["document"]["status"] == "failed"
    assert err["document"]["content"].startswith("NOTICE")  # partial text kept for the lawyer
    doc_id = err["document"]["id"]
    r = await client.post(
        f"/v1/studio/documents/{doc_id}/revise",
        json={"instructions": "Finish it"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 409
    assert (
        await client.get(f"/v1/studio/documents/{doc_id}/export", headers=world.partner_a.headers)
    ).status_code == 200
    # Finishing it by hand makes it a complete document.
    r = await client.patch(
        f"/v1/studio/documents/{doc_id}",
        json={"content": "NOTICE\n\n1. Done by hand."},
        headers=world.partner_a.headers,
    )
    assert r.json()["status"] == "complete" and r.json()["error"] is None


def test_clean_tidies_model_output_and_titles_name_the_party() -> None:
    from app.modules.studio.prompts import clean, describe, split_notes
    from app.modules.studio.templates import BY_KEY

    raw = "```\n## **LEGAL NOTICE**  \n\n\n\n1. 1.1 The cheque was returned.  \n2. 2.1 Pay.\nDRAFTING NOTES\n- Fill [x]\n```"
    text = clean(raw)
    assert text == "LEGAL NOTICE\n\n1.1 The cheque was returned.\n2.1 Pay.\nDRAFTING NOTES\n- Fill [x]"
    assert split_notes(text) == ("LEGAL NOTICE\n\n1.1 The cheque was returned.\n2.1 Pay.", ["Fill [x]"])
    nda = {"nda_type": "Mutual", "party_a": "Acme Robotics Private Limited, Mumbai"}
    assert describe(nda, BY_KEY["nda"]) == "Acme Robotics Private Limited"
    assert describe(nda) == "Mutual"


class LoopingBackend(ScriptedBackend):
    """Repeats one paragraph forever, as gpt-oss:20b did on a two-word request; records whether the
    studio closed the stream (which also ends the HTTP request to the model server)."""

    closed = False

    async def stream(
        self, model: str, messages: list[Message], *, max_tokens: int
    ) -> AsyncIterator[TextDelta | Notice | Usage]:
        try:
            yield TextDelta("PETITION\n\n1. The Petitioner is [name].\n")
            for n in range(2, 10_000):
                yield TextDelta(f"{n}. The Petitioner affirms that this Petition is filed in good faith.\n")
        finally:
            LoopingBackend.closed = True


async def test_a_looping_model_is_stopped_and_the_repeats_cut(client: AsyncClient, world: World) -> None:
    body = {"template": "custom", "inputs": {"document": "petition", "details": "test"}}
    with use_backend(LoopingBackend()):
        ev = await stream(client, "/v1/studio/documents", world.partner_a.headers, **body)
    assert LoopingBackend.closed
    assert len([k for k, _ in ev if k == "delta"]) < 10  # stopped at the 4th repeat, not at 10,000
    assert any(k == "notice" and "repeating" in d["text"] for k, d in ev)
    doc = ev[-1][1]
    assert doc["status"] == "complete" and "repeating" in doc["error"]
    assert doc["content"].count("good faith") == 1


async def test_hitting_the_length_limit_warns_the_lawyer(client: AsyncClient, world: World) -> None:
    class Long(ScriptedBackend):
        async def stream(
            self, model: str, messages: list[Message], *, max_tokens: int
        ) -> AsyncIterator[TextDelta | Notice | Usage]:
            yield TextDelta("DEED OF PARTNERSHIP\n\n1. The partners")
            yield Usage(500, max_tokens)

    with use_backend(Long()):
        doc = await generate(client, world.partner_a.headers, nda("long"))
    assert doc["status"] == "complete" and "length limit" in doc["error"]
