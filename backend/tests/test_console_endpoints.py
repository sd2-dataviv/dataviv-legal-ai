"""Endpoints the test console relies on: firm users, matter people, dev identities."""

from typing import Any

from httpx import AsyncClient

from tests.conftest import World


async def test_users_are_limited_to_own_firm(client: AsyncClient, world: World) -> None:
    emails = {u["email"] for u in (await client.get("/v1/users", headers=world.partner_a.headers)).json()}
    assert "associate@sharma.in" in emails and "partner@mehta.in" not in emails


async def test_matter_people_lists_members_and_walls(
    client: AsyncClient, world: World, new_matter: dict[str, Any]
) -> None:
    mid, h = new_matter["id"], world.partner_a.headers
    await client.post(f"/v1/matters/{mid}/members", json={"user_id": str(world.associate_a.id)}, headers=h)
    await client.post(
        f"/v1/matters/{mid}/walls",
        json={"user_id": str(world.paralegal_a.id), "reason": "Related to the opposing party"},
        headers=world.admin_a.headers,
    )
    people = (await client.get(f"/v1/matters/{mid}/people", headers=h)).json()
    roles = {m["email"]: m["matter_role"] for m in people["members"]}
    assert roles == {"partner@sharma.in": "lead", "associate@sharma.in": "member"}
    assert [w["user_id"] for w in people["walls"]] == [str(world.paralegal_a.id)]
    assert (await client.get(f"/v1/matters/{mid}/people", headers=world.partner_b.headers)).status_code == 404


async def test_dev_identities_issue_working_tokens(client: AsyncClient, world: World) -> None:
    ids = (await client.get("/v1/dev/identities")).json()
    me = next(i for i in ids if i["email"] == "partner@mehta.in")
    r = await client.get("/v1/me", headers={"Authorization": f"Bearer {me['access_token']}"})
    assert r.json()["firm_id"] == str(world.firm_b) and me["firm_name"] == "Mehta Legal LLP"
