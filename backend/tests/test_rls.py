"""NFR-01 / ADR-002: database-level isolation, independent of application code."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import get_engine, tenant_session
from tests.conftest import World


async def test_every_table_has_rls(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        missing = (
            await conn.scalars(
                text(
                    "SELECT relname FROM pg_class WHERE relkind = 'r' "
                    "AND relnamespace = 'public'::regnamespace AND relname <> 'alembic_version' "
                    "AND NOT relrowsecurity"
                )
            )
        ).all()
    assert missing == [], f"Tables without row-level security: {missing}"


async def test_no_firm_context_sees_nothing(world: World, new_matter: dict) -> None:
    async with get_engine().connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM matters")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM users")) == 0


async def test_firm_context_sees_only_own_rows(world: World, new_matter: dict) -> None:
    async with tenant_session(world.firm_b) as s:
        firms = (await s.scalars(text("SELECT DISTINCT firm_id FROM matters"))).all()
        assert all(f == world.firm_b for f in firms)
        n = await s.scalar(text("SELECT count(*) FROM matters WHERE id = :m"), {"m": new_matter["id"]})
        assert n == 0


async def test_cannot_write_into_another_firm(world: World) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with tenant_session(world.firm_a) as s:
            await s.execute(
                text(
                    "INSERT INTO matters (firm_id, title, client_name, created_by) VALUES (:f, 'x', 'x', :u)"
                ),
                {"f": world.firm_b, "u": world.partner_b.id},
            )


async def test_app_user_cannot_alter_audit(world: World) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.firm_a) as s:
            await s.execute(text("UPDATE audit_events SET action = 'tampered'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with tenant_session(world.firm_a) as s:
            await s.execute(text("DELETE FROM audit_events"))


async def test_firm_context_does_not_leak_across_pooled_connections(world: World) -> None:
    async with tenant_session(world.firm_a) as s:
        assert await s.scalar(text("SELECT current_setting('app.firm_id', true)")) == str(world.firm_a)
    # same pool, new transaction, no context set
    async with get_engine().connect() as conn:
        assert await conn.scalar(text("SELECT NULLIF(current_setting('app.firm_id', true), '')")) is None
