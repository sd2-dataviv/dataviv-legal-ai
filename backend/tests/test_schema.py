"""Models mirror the SQL migrations (db/base.py): every mapped column exists in the migrated schema."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import app.main  # noqa: F401 - imports every module, registering all models
from app.db.base import Base


async def test_models_match_migrated_schema(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
                )
            )
        ).all()
    columns: dict[str, set[str]] = {}
    for table, column in rows:
        columns.setdefault(table, set()).add(column)
    missing = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.tables.values()
        for c in t.columns
        if c.name not in columns.get(t.name, set())
    ]
    assert missing == [], f"Mapped columns missing from the database: {missing}"
