"""FND-05: append-only, hash-chained audit trail."""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import verify_chain
from app.core.security import issue_dev_token
from app.db.session import tenant_session
from tests.conftest import World, _user, make_firm


async def test_matter_creation_is_audited_and_chain_verifies(
    client: AsyncClient, world: World, new_matter: dict
) -> None:
    async with tenant_session(world.firm_a) as s:
        n = await s.scalar(
            text("SELECT count(*) FROM audit_events WHERE action = 'matter.create' AND object_id = :m"),
            {"m": new_matter["id"]},
        )
        assert n == 1
        assert await verify_chain(s, world.firm_a)


async def test_tampering_breaks_the_chain(client: AsyncClient, owner_engine: AsyncEngine) -> None:
    # Isolated firm so tampering does not affect other tests.
    firm = await make_firm(owner_engine, "Tamper Test LLP")
    partner = await _user(owner_engine, firm, "partner", "p@tamper.in")
    headers = {"Authorization": f"Bearer {issue_dev_token(partner.id, firm, 'partner', partner.email)}"}
    for i in range(3):
        body = {"title": f"Matter {i}", "client_name": "C"}
        r = await client.post("/v1/matters", json=body, headers=headers)
        assert r.status_code == 201

    async with tenant_session(firm) as s:
        assert await verify_chain(s, firm)

    # Only the schema owner can do this; app_user is denied UPDATE (see test_rls).
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE audit_events SET detail = '{\"title\": \"edited\"}' "
                 "WHERE id = (SELECT min(id) FROM audit_events WHERE firm_id = :f)"),
            {"f": firm},
        )

    async with tenant_session(firm) as s:
        assert not await verify_chain(s, firm)
