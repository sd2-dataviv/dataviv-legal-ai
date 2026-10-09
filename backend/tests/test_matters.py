"""FND-01, FND-03, FND-04: matters, membership, ethical walls, and ADR-007 (404 not 403)."""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import World


async def test_partner_opens_matter_and_leads_it(new_matter: dict[str, object]) -> None:
    assert new_matter["my_role"] == "lead"
    assert new_matter["cnr"] == "DLHC010230802020"


async def test_invalid_cnr_rejected(client: AsyncClient, world: World) -> None:
    r = await client.post(
        "/v1/matters", json={"title": "Bad CNR", "client_name": "X", "cnr": "123"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 422


async def test_paralegal_cannot_open_matter(client: AsyncClient, world: World) -> None:
    r = await client.post(
        "/v1/matters", json={"title": "Not allowed", "client_name": "X"}, headers=world.paralegal_a.headers
    )
    assert r.status_code == 403


async def test_non_member_same_firm_gets_404(client: AsyncClient, world: World, new_matter: dict) -> None:
    r = await client.get(f"/v1/matters/{new_matter['id']}", headers=world.associate_a.headers)
    assert r.status_code == 404
    listed = await client.get("/v1/matters", headers=world.associate_a.headers)
    assert new_matter["id"] not in {m["id"] for m in listed.json()}


async def test_admin_has_no_implicit_content_access(
    client: AsyncClient, world: World, new_matter: dict
) -> None:
    r = await client.get(f"/v1/matters/{new_matter['id']}", headers=world.admin_a.headers)
    assert r.status_code == 404


async def test_other_firm_gets_404(client: AsyncClient, world: World, new_matter: dict) -> None:
    r = await client.get(f"/v1/matters/{new_matter['id']}", headers=world.partner_b.headers)
    assert r.status_code == 404
    listed = await client.get("/v1/matters", headers=world.partner_b.headers)
    assert new_matter["id"] not in {m["id"] for m in listed.json()}


async def test_add_member_grants_access(client: AsyncClient, world: World, new_matter: dict) -> None:
    mid = new_matter["id"]
    r = await client.post(
        f"/v1/matters/{mid}/members", json={"user_id": str(world.associate_a.id)},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 201
    r = await client.get(f"/v1/matters/{mid}", headers=world.associate_a.headers)
    assert r.status_code == 200
    assert r.json()["my_role"] == "member"


async def test_member_cannot_add_members(client: AsyncClient, world: World, new_matter: dict) -> None:
    mid = new_matter["id"]
    await client.post(
        f"/v1/matters/{mid}/members", json={"user_id": str(world.associate_a.id)},
        headers=world.partner_a.headers,
    )
    r = await client.post(
        f"/v1/matters/{mid}/members", json={"user_id": str(world.paralegal_a.id)},
        headers=world.associate_a.headers,
    )
    assert r.status_code == 403


async def test_cannot_add_user_from_other_firm(client: AsyncClient, world: World, new_matter: dict) -> None:
    r = await client.post(
        f"/v1/matters/{new_matter['id']}/members", json={"user_id": str(world.partner_b.id)},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 404


async def test_ethical_wall_overrides_membership(client: AsyncClient, world: World, new_matter: dict) -> None:
    mid = new_matter["id"]
    await client.post(
        f"/v1/matters/{mid}/members", json={"user_id": str(world.associate_a.id)},
        headers=world.partner_a.headers,
    )
    r = await client.post(
        f"/v1/matters/{mid}/walls",
        json={"user_id": str(world.associate_a.id), "reason": "Previously acted for opposing party"},
        headers=world.admin_a.headers,
    )
    assert r.status_code == 201
    assert (await client.get(f"/v1/matters/{mid}", headers=world.associate_a.headers)).status_code == 404
    r = await client.post(
        f"/v1/matters/{mid}/members", json={"user_id": str(world.associate_a.id)},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 409


async def test_only_admin_raises_walls(client: AsyncClient, world: World, new_matter: dict) -> None:
    r = await client.post(
        f"/v1/matters/{new_matter['id']}/walls",
        json={"user_id": str(world.paralegal_a.id), "reason": "Conflict check hit"},
        headers=world.partner_a.headers,
    )
    assert r.status_code == 403


async def test_access_denial_is_audited(
    client: AsyncClient, world: World, new_matter: dict, owner_engine: AsyncEngine
) -> None:
    await client.get(f"/v1/matters/{new_matter['id']}", headers=world.associate_a.headers)
    async with owner_engine.connect() as conn:
        n = await conn.scalar(
            text("SELECT count(*) FROM audit_events WHERE action = 'matter.access_denied' "
                 "AND actor_id = :u AND object_id = :m"),
            {"u": world.associate_a.id, "m": new_matter["id"]},
        )
    assert n == 1
