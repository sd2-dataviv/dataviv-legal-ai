"""FastAPI application factory. Run: `uvicorn app.main:app --reload` from backend/."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import system
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.db.session import dispose_engine
from app.modules.matters.router import router as matters_router
from app.modules.vault.router import router as vault_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    if get_settings().run_worker_in_api:
        from app.worker import worker_loop

        logging.getLogger("dataviv").setLevel(logging.INFO)
        task = asyncio.create_task(worker_loop(stop))
    yield
    stop.set()
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(matters_router)
    app.include_router(vault_router)
    # Step 2+: assistant, workflows, agents routers are registered here.
    return app


app = create_app()
