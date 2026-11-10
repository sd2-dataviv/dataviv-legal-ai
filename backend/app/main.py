"""FastAPI application factory. Run: `uvicorn app.main:app --reload` from backend/."""

import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import system
from app.core.config import get_settings
from app.core.embeddings import get_embedder
from app.core.errors import install_error_handlers
from app.db.session import dispose_engine
from app.modules.agents.router import router as agents_router
from app.modules.assistant.router import router as assistant_router
from app.modules.drafts.router import router as drafts_router
from app.modules.esign.router import router as esign_router
from app.modules.matters.router import router as matters_router
from app.modules.review.router import router as review_router
from app.modules.studio.router import router as studio_router
from app.modules.vault.router import router as vault_router
from app.modules.workflows.router import router as workflows_router


def _app_logging() -> None:
    """Our own log lines (dataviv.*) at INFO on stdout, where a hosting platform's Logs tab reads them."""
    log = logging.getLogger("dataviv")
    log.setLevel(logging.INFO)
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)
        log.propagate = False


async def _preload_embeddings() -> None:
    """Load the local search model in the background, so the first search (or agent call) is not the one that
    waits a minute or more for it. Failures are logged; search then loads it on first use as before."""
    log = logging.getLogger("dataviv.embeddings")
    embedder = get_embedder()
    preload = getattr(embedder, "preload", None)
    if preload is None:
        return
    log.info("[search] loading the %s model in the background", embedder.name if embedder else "")
    t0 = time.monotonic()
    try:
        await asyncio.to_thread(preload)
        log.info("[search] model ready in %.0fs", time.monotonic() - t0)
    except Exception as exc:  # logged; the first search loads it again
        log.warning(
            "[search] model preload failed (%s: %s); it will load on first use", type(exc).__name__, exc
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    s = get_settings()
    if s.env != "test":
        _app_logging()
    if s.run_worker_in_api:
        from app.worker import worker_loop

        logging.getLogger("dataviv").setLevel(logging.INFO)
        tasks.append(asyncio.create_task(worker_loop(stop)))
    if s.embeddings_backend in ("local", "ollama") and s.embeddings_preload:
        tasks.append(asyncio.create_task(_preload_embeddings()))
    yield
    stop.set()
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="0.4.0", lifespan=lifespan)
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
    app.include_router(assistant_router)
    app.include_router(drafts_router)
    app.include_router(esign_router)
    app.include_router(review_router)
    app.include_router(workflows_router)
    app.include_router(agents_router)
    app.include_router(studio_router)  # drafting studio add-on

    ui = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if ui.is_dir():  # built test console (npm run build) served at /ui, same origin as the API
        app.mount("/ui", StaticFiles(directory=ui, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            """Hosting platforms open an app at "/": send people to the console."""
            return RedirectResponse("/ui/")

    return app


app = create_app()
