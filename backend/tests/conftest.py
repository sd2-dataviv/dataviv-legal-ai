"""Test fixtures. Tests run against a REAL PostgreSQL 16 with RLS on (design §7).

Start one with `docker compose -f infra/docker-compose.yml up -d postgres`, then `pytest`.
The schema is rebuilt from migrations/sql at the start of each test session.
"""

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("DATAVIV_ENV", "test")
os.environ.setdefault(
    "DATAVIV_DATABASE_URL", "postgresql+asyncpg://app_user:app_user_dev_only@localhost:5432/dataviv_test"
)
os.environ.setdefault(
    "DATAVIV_DATABASE_OWNER_URL", "postgresql+asyncpg://dataviv:dataviv_dev_only@localhost:5432/dataviv_test"
)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings
from app.core.security import Role, issue_dev_token
from app.db.session import dispose_engine
from app.main import create_app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run every async test on the session event loop, so pooled DB connections stay valid."""
    marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if pytest_asyncio.is_async_test(item):
            item.add_marker(marker, append=False)


SQL_DIR = Path(__file__).resolve().parent.parent / "migrations" / "sql"


@dataclass(frozen=True)
class SeedUser:
    id: uuid.UUID
    firm_id: uuid.UUID
    role: Role
    email: str

    @property
    def headers(self) -> dict[str, str]:
        token = issue_dev_token(self.id, self.firm_id, self.role, self.email)
        return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class World:
    firm_a: uuid.UUID
    firm_b: uuid.UUID
    partner_a: SeedUser
    associate_a: SeedUser
    paralegal_a: SeedUser
    admin_a: SeedUser
    partner_b: SeedUser


async def run_script(engine: AsyncEngine, sql: str) -> None:
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(sql)  # type: ignore[union-attr]
        await conn.commit()


@pytest.fixture(scope="session")
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_owner_url)
    await run_script(engine, (SQL_DIR / "0001_foundation_down.sql").read_text())
    await run_script(engine, (SQL_DIR / "0001_foundation.sql").read_text())
    yield engine
    await engine.dispose()


async def _user(engine: AsyncEngine, firm_id: uuid.UUID, role: Role, email: str) -> SeedUser:
    uid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, firm_id, email, name, role) VALUES (:id, :f, :e, :n, :r)"),
            {"id": uid, "f": firm_id, "e": email, "n": email.split("@")[0], "r": role},
        )
    return SeedUser(uid, firm_id, role, email)


async def make_firm(engine: AsyncEngine, name: str) -> uuid.UUID:
    fid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO firms (id, name) VALUES (:id, :n)"), {"id": fid, "n": name})
    return fid


@pytest.fixture(scope="session")
async def world(owner_engine: AsyncEngine) -> World:
    """Seeded as the schema owner (bypasses RLS). Two firms; firm A has four roles."""
    a = await make_firm(owner_engine, "Sharma & Associates")
    b = await make_firm(owner_engine, "Mehta Legal LLP")
    return World(
        firm_a=a,
        firm_b=b,
        partner_a=await _user(owner_engine, a, "partner", "partner@sharma.in"),
        associate_a=await _user(owner_engine, a, "associate", "associate@sharma.in"),
        paralegal_a=await _user(owner_engine, a, "paralegal", "paralegal@sharma.in"),
        admin_a=await _user(owner_engine, a, "admin", "admin@sharma.in"),
        partner_b=await _user(owner_engine, b, "partner", "partner@mehta.in"),
    )


@pytest.fixture(scope="session")
async def client(owner_engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await dispose_engine()


@pytest.fixture
async def new_matter(client: AsyncClient, world: World) -> dict[str, object]:
    """A fresh matter led by partner A, so tests never share mutable state."""
    r = await client.post(
        "/v1/matters",
        json={
            "title": f"State v Accused {uuid.uuid4().hex[:6]}",
            "client_name": "Accused",
            "forum": "Delhi High Court",
            "cnr": "DLHC010230802020",
        },
        headers=world.partner_a.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]
