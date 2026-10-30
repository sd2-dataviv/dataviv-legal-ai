"""Database sessions.

`tenant_session(firm_id)` opens a transaction and sets `app.firm_id` for it, so Postgres RLS
restricts every statement to that firm. The setting is transaction-local (`set_config(..., true)`),
so a pooled connection can never carry one firm's context into another request.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

log = logging.getLogger("dataviv.db")
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine, _sessionmaker = None, None


@asynccontextmanager
async def tenant_session(firm_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """One transaction, scoped to one firm. Commits on success, rolls back on error.

    Work registered with ``after_commit`` runs once the commit has succeeded (never after a rollback).
    """
    async with get_sessionmaker()() as session:
        try:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.firm_id', :fid, true)"), {"fid": str(firm_id)}
                )
                yield session
        except BaseException:
            for undo in session.info.pop("after_rollback", []):
                undo()
            raise
        hooks: list[Callable[[], Awaitable[None]]] = session.info.pop("after_commit", [])
    for hook in hooks:
        try:
            await hook()
        except Exception:
            log.exception("after-commit hook failed")


def after_commit(db: AsyncSession, hook: Callable[[], Awaitable[None]]) -> None:
    """Run ``hook`` after this session's transaction commits (e.g. tell live collaborators to refresh)."""
    db.info.setdefault("after_commit", []).append(hook)


def after_rollback(db: AsyncSession, undo: Callable[[], None]) -> None:
    """Run ``undo`` (synchronous, quick) if this session's transaction rolls back instead."""
    db.info.setdefault("after_rollback", []).append(undo)
