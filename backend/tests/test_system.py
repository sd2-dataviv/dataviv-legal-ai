"""FND-02: authentication and system endpoints."""

from httpx import AsyncClient

from tests.conftest import World


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_me_requires_token(client: AsyncClient) -> None:
    r = await client.get("/v1/me")
    assert r.status_code == 401
    assert r.headers["content-type"] == "application/problem+json"


async def test_me_rejects_tampered_token(client: AsyncClient, world: World) -> None:
    token = world.partner_a.headers["Authorization"] + "x"
    r = await client.get("/v1/me", headers={"Authorization": token})
    assert r.status_code == 401


async def test_me_returns_principal(client: AsyncClient, world: World) -> None:
    r = await client.get("/v1/me", headers=world.partner_a.headers)
    assert r.status_code == 200
    assert r.json() == {
        "user_id": str(world.partner_a.id),
        "firm_id": str(world.firm_a),
        "role": "partner",
        "email": "partner@sharma.in",
    }
