"""DRF-12..15: send an approved draft for e-signature, track signers, keep the signed copy (Step 2e)."""

import base64
import hashlib
import hmac
import io
import json
import uuid
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import esign as provider
from app.core.config import Settings, get_settings
from app.core.esign import DocuSign, ESignError, FakeESign
from tests.conftest import World
from tests.factories import make_pdf
from tests.test_drafts_api import create, get, team

RunJobs = Callable[[], Awaitable[int]]
NOTICE = (
    "To,\nLotus Holdings Private Limited\n\n1. Our client requires the filings to be brought up to date.\n"
)


async def approved_draft(
    client: AsyncClient, world: World, matter: dict[str, Any], title: str = "Notice", *, teamed: bool = False
) -> str:
    """Associate A drafts (member), partner A (lead) approves version 1."""
    mid = str(matter["id"]) if teamed else await team(client, world, matter)
    d = await create(client, mid, world.associate_a.headers, title=title, kind="notice", content=NOTICE)
    r = await client.post(
        f"/v1/drafts/{d['id']}/review",
        json={"reviewer_id": str(world.partner_a.id)},
        headers=world.associate_a.headers,
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/v1/drafts/{d['id']}/review/decision", json={"decision": "approve"}, headers=world.partner_a.headers
    )
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    return str(d["id"])


def signers(world: World) -> list[dict[str, Any]]:
    return [
        {"name": "Asha Partner", "email": world.partner_a.email, "routing_order": 1},
        {"name": "Ravi Client", "email": "ravi@lotus.example", "routing_order": 2},
    ]


async def send(
    client: AsyncClient, did: str, headers: dict[str, str], body: dict[str, Any]
) -> httpx.Response:
    return await client.post(f"/v1/drafts/{did}/signatures", json=body, headers=headers)


async def simulate(
    client: AsyncClient, rid: str, headers: dict[str, str], action: str, **kw: Any
) -> dict[str, Any]:
    r = await client.post(
        f"/v1/dev/signatures/{rid}/simulate", json={"action": action, **kw}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- the flow in test mode


async def test_signers_sign_in_order_and_the_signed_copy_lands_in_the_vault(
    client: AsyncClient, world: World, new_matter: dict[str, Any], run_jobs: RunJobs
) -> None:
    did = await approved_draft(client, world, new_matter)
    lead = world.partner_a.headers
    st = (await client.get("/v1/esign/status", headers=lead)).json()
    assert st["backend"] == "fake" and st["ready"] is True and st["environment"] == "test"

    r = await send(
        client, did, lead, {"signers": signers(world), "confirm_not_excluded": True, "message": "Please sign"}
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["status"] == "sent" and s["version_no"] == 1 and s["provider"] == "fake"
    assert s["subject"] == "Please sign: Notice (version 1)" and s["sent_by"]["id"] == str(world.partner_a.id)
    first, second = s["signers"]
    assert (first["status"], second["status"]) == ("sent", "created")  # routing order: the client waits
    assert (
        first["user"]["id"] == str(world.partner_a.id) and second["user"] is None
    )  # firm member linked by e-mail

    s = await simulate(client, s["id"], lead, "sign_next")
    assert (
        s["status"] == "delivered"
        and s["signers"][0]["status"] == "signed"
        and s["signers"][1]["status"] == "sent"
    )
    assert s["signed_document_id"] is None

    s = await simulate(client, s["id"], lead, "sign_next")
    assert s["status"] == "completed" and all(
        x["status"] == "signed" and x["signed_at"] for x in s["signers"]
    )
    assert s["signed_document_id"] and s["certificate_document_id"] and s["completed_at"]

    await run_jobs()  # the signed PDF and certificate are ingested like any upload
    doc = (
        await client.get(f"/v1/documents/{s['signed_document_id']}", headers=world.associate_a.headers)
    ).json()
    assert doc["title"] == "Notice v1 - signed.pdf" and doc["status"] == "indexed", doc
    cert = (await client.get(f"/v1/documents/{s['certificate_document_id']}", headers=lead)).json()
    assert cert["title"] == "Notice v1 - signing certificate.pdf"
    hits = (
        await client.post("/v1/search", json={"query": "Ravi Client signed", "mode": "keyword"}, headers=lead)
    ).json()
    assert any(h["document_id"] == s["signed_document_id"] for h in hits["hits"])

    d = await get(client, did, world.paralegal_a.headers)  # viewers see the history too
    assert d["signatures"][0]["status"] == "completed"
    actions = [a["action"] for a in d["activity"]]
    assert "draft.signature_sent" in actions and "draft.signed" in actions

    again = await simulate_raw(client, s["id"], lead, "sign_next")
    assert again.status_code == 409  # nothing left to sign


async def simulate_raw(client: AsyncClient, rid: str, headers: dict[str, str], action: str) -> httpx.Response:
    return await client.post(f"/v1/dev/signatures/{rid}/simulate", json={"action": action}, headers=headers)


async def test_only_a_lead_or_partner_sends_and_only_an_approved_draft(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid = await team(client, world, new_matter)
    body = {"signers": signers(world), "confirm_not_excluded": True}
    unapproved = await create(client, mid, world.associate_a.headers, title="Draft", content="Text")
    r = await send(client, unapproved["id"], world.partner_a.headers, body)
    assert r.status_code == 409 and "approved" in r.json()["detail"]

    did = await approved_draft(client, world, new_matter, "Approved notice", teamed=True)
    assert (await send(client, did, world.associate_a.headers, body)).status_code == 403  # member
    assert (await send(client, did, world.paralegal_a.headers, body)).status_code == 403  # viewer
    assert (await send(client, did, world.partner_b.headers, body)).status_code == 404  # other firm
    r = await send(client, did, world.partner_a.headers, {"signers": signers(world)})
    assert r.status_code == 422 and "powers of attorney" in r.json()["detail"]  # IT Act exclusions
    dup = [signers(world)[0], {**signers(world)[1], "email": world.partner_a.email.upper()}]
    r = await send(client, did, world.partner_a.headers, {"signers": dup, "confirm_not_excluded": True})
    assert r.status_code == 422
    r = await send(
        client,
        did,
        world.partner_a.headers,
        {"signers": [{"name": "X", "email": "not-an-email"}], "confirm_not_excluded": True},
    )
    assert r.status_code == 422

    assert (await send(client, did, world.partner_a.headers, body)).status_code == 201
    r = await send(client, did, world.partner_a.headers, body)
    assert r.status_code == 409 and "already out for signature" in r.json()["detail"]


async def test_a_decline_or_a_void_ends_the_envelope_and_it_can_be_sent_again(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    did = await approved_draft(client, world, new_matter, "Reply")
    lead, member = world.partner_a.headers, world.associate_a.headers
    body = {"signers": signers(world), "confirm_not_excluded": True}

    s = (await send(client, did, lead, body)).json()
    s = await simulate(client, s["id"], lead, "decline", reason="Clause 1 is wrong")
    assert s["status"] == "declined" and s["signers"][0]["declined_reason"] == "Clause 1 is wrong"
    assert s["signed_document_id"] is None
    detail = await get(client, did, lead)
    declined = next(a for a in detail["activity"] if a["action"] == "draft.signature_declined")
    assert declined["detail"]["reason"] == "Clause 1 is wrong"

    s = (await send(client, did, lead, body)).json()  # a declined envelope no longer blocks a new one
    r = await client.post(f"/v1/signatures/{s['id']}/void", json={"reason": "Wrong client"}, headers=member)
    assert r.status_code == 403
    r = await client.post(f"/v1/signatures/{s['id']}/void", json={"reason": "Wrong client"}, headers=lead)
    assert (
        r.status_code == 200
        and r.json()["status"] == "voided"
        and r.json()["voided_reason"] == "Wrong client"
    )
    assert all(x["status"] == "voided" for x in r.json()["signers"])
    r = await client.post(f"/v1/signatures/{s['id']}/void", json={"reason": "again"}, headers=lead)
    assert r.status_code == 409
    assert (await send(client, did, lead, body)).status_code == 201
    listing = (await client.get(f"/v1/drafts/{did}/signatures", headers=member)).json()
    assert [x["status"] for x in listing] == ["sent", "voided", "declined"]  # newest first


async def test_status_is_visible_to_the_matter_only(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    did = await approved_draft(client, world, new_matter, "Visibility")
    s = (
        await send(
            client, did, world.partner_a.headers, {"signers": signers(world), "confirm_not_excluded": True}
        )
    ).json()
    viewer, stranger = world.paralegal_a.headers, world.partner_b.headers
    assert (await client.post(f"/v1/signatures/{s['id']}/refresh", headers=viewer)).status_code == 200
    assert (await client.get(f"/v1/drafts/{did}/signatures", headers=viewer)).status_code == 200
    assert (await simulate_raw(client, s["id"], viewer, "sign_next")).status_code == 403
    assert (await client.post(f"/v1/signatures/{s['id']}/refresh", headers=stranger)).status_code == 404
    assert (await client.get(f"/v1/drafts/{did}/signatures", headers=stranger)).status_code == 404
    walled = await client.post(
        f"/v1/matters/{new_matter['id']}/walls",
        json={"user_id": str(world.associate_a.id), "reason": "conflict"},
        headers=world.admin_a.headers,
    )
    assert walled.status_code == 201, walled.text
    assert (
        await client.post(f"/v1/signatures/{s['id']}/refresh", headers=world.associate_a.headers)
    ).status_code == 404


@pytest.fixture
def poll_now() -> Iterator[None]:
    """Make every open envelope due for a check."""
    s = get_settings()
    old = s.esign_poll_seconds
    s.esign_poll_seconds = 0
    yield
    s.esign_poll_seconds = old


async def test_the_background_sweep_finds_signatures_made_outside_the_app(
    client: AsyncClient, world: World, new_matter: dict[str, Any], poll_now: None
) -> None:
    from app.worker import sweep_signatures

    did = await approved_draft(client, world, new_matter, "Sweep")
    s = (
        await send(
            client,
            did,
            world.partner_a.headers,
            {"signers": signers(world)[:1], "confirm_not_excluded": True},
        )
    ).json()
    fake = provider.backend()
    assert isinstance(fake, FakeESign)
    fake.act(s["envelope_id"], "sign_all")  # the signer signs at the provider; the app has not heard yet
    assert (await get(client, did, world.partner_a.headers))["signatures"][0]["status"] == "sent"
    assert await sweep_signatures() >= 1  # the worker checks open envelopes without any firm context
    after = (await get(client, did, world.partner_a.headers))["signatures"][0]
    assert after["status"] == "completed" and after["signed_document_id"] and after["last_checked_at"]
    signed = next(
        a
        for a in (await get(client, did, world.partner_a.headers))["activity"]
        if a["action"] == "draft.signed"
    )
    assert signed["actor"]["id"] == str(world.partner_a.id)  # recorded on behalf of the sender


async def test_connect_callbacks_need_a_valid_hmac(
    client: AsyncClient, world: World, new_matter: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    did = await approved_draft(client, world, new_matter, "Callback")
    s = (
        await send(
            client,
            did,
            world.partner_a.headers,
            {"signers": signers(world)[:1], "confirm_not_excluded": True},
        )
    ).json()
    async with owner_engine.begin() as conn:  # pretend it went through DocuSign
        await conn.execute(
            text("UPDATE signature_requests SET provider = 'docusign' WHERE id = :i"), {"i": s["id"]}
        )
    body = json.dumps({"event": "envelope-completed", "data": {"envelopeId": s["envelope_id"]}}).encode()
    settings = get_settings()
    settings.docusign_connect_secret = SecretStr("connect-key")
    try:
        good = base64.b64encode(hmac.new(b"connect-key", body, hashlib.sha256).digest()).decode()
        url = "/v1/esign/docusign/connect"
        assert (await client.post(url, content=body)).status_code == 401
        assert (
            await client.post(url, content=body, headers={"X-DocuSign-Signature-1": "bad"})
        ).status_code == 401
        r = await client.post(
            url, content=body, headers={"X-DocuSign-Signature-1": "x", "X-DocuSign-Signature-2": good}
        )
        assert r.status_code == 200 and r.json() == {"matched": True}
        other = json.dumps({"data": {"envelopeId": str(uuid.uuid4())}}).encode()
        sig = base64.b64encode(hmac.new(b"connect-key", other, hashlib.sha256).digest()).decode()
        r = await client.post(url, content=other, headers={"X-DocuSign-Signature-1": sig})
        assert r.status_code == 200 and r.json() == {"matched": False}  # not ours: acknowledged, ignored
    finally:
        settings.docusign_connect_secret = None
    # The matched envelope was checked: the server runs the test backend, so it says so instead of guessing
    row = (await get(client, did, world.partner_a.headers))["signatures"][0]
    assert row["last_error"] and "sent with docusign" in row["last_error"]


# --------------------------------------------------------------------------- the real DocuSign client


@pytest.fixture(scope="module")
def rsa_pair() -> tuple[str, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


class MockDocuSign:
    """Just enough of DocuSign (auth server + eSignature REST v2.1) to exercise the client end to end."""

    AUTH, BASE = "https://account-d.docusign.com", "https://demo.docusign.net/restapi/v2.1/accounts/acc-1"

    def __init__(self, public_key: bytes, consent: bool = True) -> None:
        self.public_key, self.consent = public_key, consent
        self.tokens = 0
        self.envelopes: dict[str, dict[str, Any]] = {}

    def complete(self, envelope_id: str) -> None:
        env = self.envelopes[envelope_id]
        env["status"] = "completed"
        for x in env["body"]["recipients"]["signers"]:
            x["_status"] = "completed"

    def handle(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == f"{self.AUTH}/oauth/token":
            form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            if not self.consent:
                return httpx.Response(400, json={"error": "consent_required"})
            claims = jwt.decode(
                form["assertion"], self.public_key, algorithms=["RS256"], audience="account-d.docusign.com"
            )
            assert claims["iss"] == "ik-123" and claims["sub"] == "user-456"
            assert claims["scope"] == "signature impersonation" and claims["exp"] - claims["iat"] == 3600
            assert form["grant_type"] == "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
            self.tokens += 1
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer tok-1"
        if url == f"{self.AUTH}/oauth/userinfo":
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {"account_id": "acc-0", "is_default": False, "base_uri": "https://na2.docusign.net"},
                        {
                            "account_id": "acc-1",
                            "is_default": True,
                            "base_uri": "https://demo.docusign.net",
                            "account_name": "Sharma & Associates (sandbox)",
                        },
                    ]
                },
            )
        assert url.startswith(self.BASE), url
        path = url[len(self.BASE) :]
        if req.method == "POST" and path == "/envelopes":
            eid = str(uuid.uuid4())
            body = json.loads(req.content)
            for x in body["recipients"]["signers"]:
                x["_status"] = "sent" if x["routingOrder"] == "1" else "created"
            self.envelopes[eid] = {"status": "sent", "body": body}
            return httpx.Response(201, json={"envelopeId": eid, "status": "sent"})
        parts = path.strip("/").split("/")
        env = self.envelopes[parts[1]]
        if req.method == "GET" and len(parts) == 2:
            done = "2026-10-19T09:30:00.0000000Z" if env["status"] == "completed" else None
            return httpx.Response(
                200,
                json={
                    "status": env["status"],
                    "completedDateTime": done,
                    "voidedReason": env.get("voidedReason"),
                },
            )
        if req.method == "GET" and parts[2] == "recipients":
            return httpx.Response(
                200,
                json={
                    "signers": [
                        {
                            "recipientId": x["recipientId"],
                            "status": x["_status"],
                            "signedDateTime": "2026-10-19T09:29:00Z" if x["_status"] == "completed" else None,
                        }
                        for x in env["body"]["recipients"]["signers"]
                    ]
                },
            )
        if req.method == "PUT" and len(parts) == 2:
            update = json.loads(req.content)
            env["status"], env["voidedReason"] = update["status"], update["voidedReason"]
            return httpx.Response(200, json={"envelopeId": parts[1]})
        if req.method == "GET" and parts[2:] == ["documents", "combined"]:
            return httpx.Response(
                200, content=make_pdf([["Signed notice", "Signed by Asha Partner via DocuSign"]])
            )
        if req.method == "GET" and parts[2:] == ["documents", "certificate"]:
            return httpx.Response(200, content=make_pdf([["Certificate of Completion"]]))
        return httpx.Response(404, json={"errorCode": "NOT_FOUND", "message": path})


def docusign_settings(private_key: str) -> Settings:
    # _env_file=None and explicit Nones: a developer's real DocuSign values in backend/.env must not leak in
    return Settings(
        _env_file=None,
        esign_backend="docusign",
        docusign_integration_key="ik-123",
        docusign_user_id="user-456",
        docusign_private_key=SecretStr(private_key),
        docusign_private_key_path=None,
        docusign_account_id=None,
        docusign_webhook_url=None,
        docusign_connect_secret=None,
        docusign_auth_server="account-d.docusign.com",
    )


@pytest.fixture
def docusign(rsa_pair: tuple[str, bytes], monkeypatch: pytest.MonkeyPatch) -> MockDocuSign:
    server = MockDocuSign(rsa_pair[1])
    client = DocuSign(docusign_settings(rsa_pair[0]), transport=httpx.MockTransport(server.handle))
    monkeypatch.setattr(provider, "backend", lambda transport=None: client)
    return server


async def test_docusign_envelope_carries_the_approved_version_with_invisible_anchors(
    client: AsyncClient, world: World, new_matter: dict[str, Any], docusign: MockDocuSign, run_jobs: RunJobs
) -> None:
    did = await approved_draft(client, world, new_matter, "Notice to Lotus")
    lead = world.partner_a.headers
    st = (await client.get("/v1/esign/status", headers=lead)).json()
    assert st == {
        "backend": "fake",
        "ready": True,
        "environment": "sandbox",
        "account_name": "Sharma & Associates (sandbox)",
        "detail": None,
        "consent_url": None,
        "callbacks": False,
    }  # backend name comes from settings; the account from DocuSign

    r = await send(
        client, did, lead, {"signers": signers(world), "confirm_not_excluded": True, "subject": "Sign today"}
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["provider"] == "docusign"
    env = docusign.envelopes[s["envelope_id"]]["body"]
    assert env["status"] == "sent" and env["emailSubject"] == "Sign today"
    doc = env["documents"][0]
    assert doc["fileExtension"] == "docx" and doc["name"] == "Notice to Lotus v1.docx"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(doc["documentBase64"]))) as z:
        xml = z.read("word/document.xml").decode()
    assert "Our client requires the filings" in xml and "approved by" in xml
    assert (
        '<w:color w:val="FFFFFF"/>' in xml and "/dvsig1/" in xml and "/dvsig2/" in xml and "/dvdate2/" in xml
    )
    first, second = env["recipients"]["signers"]
    assert (
        first["routingOrder"] == "1"
        and second["routingOrder"] == "2"
        and second["email"] == "ravi@lotus.example"
    )
    assert first["tabs"]["signHereTabs"][0]["anchorString"] == "/dvsig1/"
    assert second["tabs"]["dateSignedTabs"][0]["anchorString"] == "/dvdate2/"

    docusign.complete(s["envelope_id"])
    s = (await client.post(f"/v1/signatures/{s['id']}/refresh", headers=world.associate_a.headers)).json()
    assert s["status"] == "completed" and all(x["status"] == "signed" for x in s["signers"])
    assert s["completed_at"].startswith("2026-10-19T09:30")
    await run_jobs()
    doc_out = (await client.get(f"/v1/documents/{s['signed_document_id']}", headers=lead)).json()
    assert doc_out["status"] == "indexed" and doc_out["title"] == "Notice to Lotus v1 - signed.pdf"
    assert docusign.tokens == 1  # the access token is reused, not fetched per call

    s2 = (await send(client, did, lead, {"signers": signers(world)[:1], "confirm_not_excluded": True})).json()
    r = await client.post(f"/v1/signatures/{s2['id']}/void", json={"reason": "Superseded"}, headers=lead)
    assert (
        r.json()["status"] == "voided"
        and docusign.envelopes[s2["envelope_id"]]["voidedReason"] == "Superseded"
    )


async def test_missing_consent_and_missing_settings_say_how_to_fix_them(rsa_pair: tuple[str, bytes]) -> None:
    server = MockDocuSign(rsa_pair[1], consent=False)
    client = DocuSign(docusign_settings(rsa_pair[0]), transport=httpx.MockTransport(server.handle))
    with pytest.raises(ESignError) as e:
        await client.account()
    assert e.value.fixable and e.value.consent_url
    assert e.value.consent_url.startswith("https://account-d.docusign.com/oauth/auth?response_type=code")
    assert (
        "client_id=ik-123" in e.value.consent_url and "scope=signature%20impersonation" in e.value.consent_url
    )

    with pytest.raises(ESignError) as e:
        DocuSign(
            Settings(
                _env_file=None,
                esign_backend="docusign",
                docusign_integration_key=None,
                docusign_user_id=None,
                docusign_private_key=None,
                docusign_private_key_path=None,
            )
        )
    assert "DATAVIV_DOCUSIGN_INTEGRATION_KEY" in e.value.message and e.value.fixable

    bad = DocuSign(docusign_settings("not a key"), transport=httpx.MockTransport(server.handle))
    with pytest.raises(ESignError) as e:
        await bad.account()
    assert "not a valid RSA key" in e.value.message
