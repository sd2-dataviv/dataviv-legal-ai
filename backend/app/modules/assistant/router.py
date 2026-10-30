import uuid
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import DB, CurrentPrincipal
from app.db.session import tenant_session
from app.modules.assistant import service
from app.modules.assistant.schemas import (
    AskRequest,
    ModelPolicyIn,
    ModelsOut,
    ThreadCreate,
    ThreadDetail,
    ThreadOut,
)

router = APIRouter(tags=["assistant"])


@router.post("/v1/matters/{matter_id}/threads", response_model=ThreadOut, status_code=status.HTTP_201_CREATED)
async def create_thread(matter_id: uuid.UUID, body: ThreadCreate, p: CurrentPrincipal, db: DB) -> ThreadOut:
    return await service.create_thread(db, p, matter_id, body.title)


@router.get("/v1/matters/{matter_id}/threads", response_model=list[ThreadOut])
async def list_threads(
    matter_id: uuid.UUID,
    p: CurrentPrincipal,
    db: DB,
    q: Annotated[str | None, Query(max_length=200, description="Search titles and messages")] = None,
) -> list[ThreadOut]:
    return await service.list_threads(db, p, matter_id, q)


@router.get("/v1/threads/{thread_id}", response_model=ThreadDetail)
async def get_thread(thread_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> ThreadDetail:
    return await service.thread_detail(db, p, thread_id)


@router.post(
    "/v1/threads/{thread_id}/messages",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "meta, notice*, delta*, then done or error",
        }
    },
)
async def ask(thread_id: uuid.UUID, body: AskRequest, p: CurrentPrincipal) -> StreamingResponse:
    """Ask, summarise or draft. Streams Server-Sent Events: `meta` (sources), `delta` (text),
    `notice` (e.g. waiting on a rate limit), then `done` (the stored answer with verified sentences)
    or `error`. Permission and validation errors are returned as problem+json before streaming."""
    async with tenant_session(p.firm_id) as db:  # committed before the stream starts
        prep = await service.prepare_ask(db, p, thread_id, body)
    return StreamingResponse(
        service.run_ask(prep),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/threads/{thread_id}/messages/{message_id}/export")
async def export_answer(thread_id: uuid.UUID, message_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> Response:
    """The answer as a Word document, with numbered citations and a sources list (AST-09)."""
    data, filename = await service.export_docx(db, p, thread_id, message_id)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/v1/assistant/models", response_model=ModelsOut)
async def list_models(p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    """Models your firm's policy allows, the default, and per-task routes ("Auto")."""
    return await service.models(db, p)


@router.put("/v1/firm/model-policy", response_model=ModelsOut)
async def put_model_policy(body: ModelPolicyIn, p: CurrentPrincipal, db: DB) -> dict[str, Any]:
    """Firm admins only: which models may be used, and which model serves each task."""
    return await service.set_model_policy(db, p, body)
