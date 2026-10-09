"""FastAPI application factory. Run: `uvicorn app.main:app --reload` from backend/."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import system
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.db.session import dispose_engine
from app.modules.matters.router import router as matters_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(matters_router)
    # Step 1+: vault, assistant, workflows, agents routers are registered here.
    return app


app = create_app()
