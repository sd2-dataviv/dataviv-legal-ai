"""Step 2f: the shared API on WorkflowHub. Other apps and AI agents call as the person asking.

The platform signs X-Wfh-User (HS256, issuer "workflowhub"); a script may send the person's platform token as
X-Auth-Token instead, which the platform's auth service vouches for (mocked here). Either way the caller acts
as the Dataviv user with the same e-mail, inside that user's firm, so RLS and matter access still apply.
"""

import time
import uuid
from collections.abc import Iterator

import httpx
import jwt
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core import platform
from app.core.config import get_settings
from tests.conftest import World

SECRET = "platform-test-secret-0123456789abcdef"
AUDIENCE = "app:dataviv_ai_os"


@pytest.fixture
def shared_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    s = get_settings()
    monkeypatch.setattr(s, "wfh_app_api_secret", SecretStr(SECRET))
    monkeypatch.setattr(s, "wfh_app_api_audience", AUDIENCE)
    monkeypatch.setattr(s, "wfh_auth_service_url", "http://auth.platform.test/api")
    monkeypatch.setattr(s, "platform_fallback_email", None)
    platform._cache.clear()
    yield
    platform.transport = None
    platform._cache.clear()


def wfh_user(
    email: str, *, secret: str = SECRET, issuer: str = "workflowhub", audience: str = AUDIENCE
) -> dict[str, str]:
    now = int(time.time())
    claims = {
        "sub": "42",
        "email": email,
        "name": "Caller",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + 300,
    }
    return {"X-Wfh-User": jwt.encode(claims, secret, algorithm="HS256")}


async def test_a_signed_platform_call_acts_as_the_user_with_the_same_email(
    client: AsyncClient, world: World, shared_api: None
) -> None:
    r = await client.get("/v1/me", headers=wfh_user("Associate@Sharma.in"))  # case does not matter
    assert r.status_code == 200, r.text
    me = r.json()
    assert (me["user_id"], me["firm_id"], me["role"]) == (
        str(world.associate_a.id),
        str(world.firm_a),
        "associate",
    )


async def test_forged_or_foreign_headers_are_refused(client: AsyncClient, shared_api: None) -> None:
    for headers in (
        wfh_user("partner@sharma.in", secret="not-the-app-secret-0123456789abcdef"),
        wfh_user("partner@sharma.in", issuer="someone-else"),
        wfh_user("partner@sharma.in", audience="app:another_app"),
        {"X-Wfh-User": "not-a-jwt"},
    ):
        r = await client.get("/v1/matters", headers=headers)
        assert r.status_code == 401, r.text


async def test_without_the_app_secret_the_shared_api_is_off(
    client: AsyncClient, shared_api: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "wfh_app_api_secret", None)
    r = await client.get("/v1/me", headers=wfh_user("partner@sharma.in"))
    assert r.status_code == 401
    assert "not enabled" in r.text


async def test_platform_callers_see_only_their_own_firm(
    client: AsyncClient, world: World, new_matter: dict[str, object], shared_api: None
) -> None:
    mine = await client.get("/v1/matters", headers=wfh_user("partner@sharma.in"))
    theirs = await client.get("/v1/matters", headers=wfh_user("partner@mehta.in"))
    assert mine.status_code == theirs.status_code == 200
    assert new_matter["id"] in {m["id"] for m in mine.json()}
    assert new_matter["id"] not in {m["id"] for m in theirs.json()}
    # and the matter itself is invisible to the other firm (404, never 403: existence is not revealed)
    r = await client.get(f"/v1/matters/{new_matter['id']}", headers=wfh_user("partner@mehta.in"))
    assert r.status_code == 404


async def test_unknown_callers_are_refused_unless_a_demo_user_is_configured(
    client: AsyncClient, world: World, shared_api: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await client.get("/v1/me", headers=wfh_user("stranger@example.com"))
    assert r.status_code == 403
    assert "No Dataviv account" in r.text

    monkeypatch.setattr(get_settings(), "platform_fallback_email", "paralegal@sharma.in")
    r = await client.get("/v1/me", headers=wfh_user("stranger@example.com"))
    assert r.status_code == 200
    assert r.json()["user_id"] == str(world.paralegal_a.id)

    # outside dev/test the fallback never applies
    monkeypatch.setattr(get_settings(), "env", "prod")
    r = await client.get("/v1/me", headers=wfh_user("stranger@example.com"))
    assert r.status_code == 403


async def test_an_email_in_two_firms_is_ambiguous(
    client: AsyncClient, world: World, owner_engine: AsyncEngine, shared_api: None
) -> None:
    email = f"twofirms-{uuid.uuid4().hex[:6]}@example.in"
    async with owner_engine.begin() as conn:
        for firm in (world.firm_a, world.firm_b):
            await conn.execute(
                text(
                    "INSERT INTO users (firm_id, email, name, role) VALUES (:f, :e, 'Two Firms', 'associate')"
                ),
                {"f": firm, "e": email},
            )
    r = await client.get("/v1/me", headers=wfh_user(email))
    assert r.status_code == 403
    assert "more than one firm" in r.text


async def test_a_platform_token_is_checked_with_the_platform_auth_service(
    client: AsyncClient, world: World, shared_api: None
) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.headers.get("X-Auth-Token") == "good-platform-token":
            return httpx.Response(
                200, json={"sub": "7", "email": "partner@sharma.in", "full_name": "Partner"}
            )
        return httpx.Response(401, json={"detail": "expired"})

    platform.transport = httpx.MockTransport(handle)
    for _ in range(2):  # the second call is served from the one-minute cache
        r = await client.get("/v1/me", headers={"X-Auth-Token": "good-platform-token"})
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == str(world.partner_a.id)
    assert calls == ["http://auth.platform.test/api/tokens/details"]

    r = await client.get("/v1/me", headers={"X-Auth-Token": "expired-token"})
    assert r.status_code == 401


async def test_our_own_bearer_tokens_still_work_and_win_over_x_auth_token(
    client: AsyncClient, world: World, shared_api: None
) -> None:
    headers = {**world.associate_a.headers, "X-Auth-Token": "ignored"}
    r = await client.get("/v1/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["user_id"] == str(world.associate_a.id)
