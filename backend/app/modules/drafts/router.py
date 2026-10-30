import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Response, WebSocket, status

from app.api.deps import DB, CurrentPrincipal
from app.modules.drafts import live, service, suggest
from app.modules.drafts.schemas import (
    CommentCreate,
    DiffOut,
    DraftCreate,
    DraftDetail,
    DraftSummary,
    DraftUpdate,
    Head,
    LiveSave,
    LiveText,
    PresenceIn,
    ReviewDecision,
    ReviewRequest,
    SuggestionCreate,
    SuggestionOut,
    VersionContent,
    VersionCreate,
    VersionOut,
)

router = APIRouter(tags=["drafts"])


# --------------------------------------------------------------------------- live co-editing (Step 2c)


@router.websocket("/v1/drafts/{draft_id}/live")
async def live_socket(ws: WebSocket, draft_id: uuid.UUID) -> None:
    """Real-time co-editing over the y-websocket protocol. Send the token as the second sub-protocol:
    `new WebSocket(url, ["dataviv", token])`. Close codes: 4401 bad token, 4403 viewers cannot edit,
    4404 no access, 4423 locked for approval, 4503 live editing unavailable."""
    await live.serve(ws, draft_id)


@router.get("/v1/drafts/{draft_id}/live/text", response_model=LiveText)
async def live_text(draft_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> LiveText:
    """The shared working copy as it is right now, including changes not yet saved as a version."""
    return await service.live_text(db, p, draft_id)


@router.post(
    "/v1/drafts/{draft_id}/live/save", response_model=VersionOut, status_code=status.HTTP_201_CREATED
)
async def live_save(draft_id: uuid.UUID, body: LiveSave, p: CurrentPrincipal, db: DB) -> VersionOut:
    """Save the shared working copy as the next version, credited to everyone who changed it."""
    return await service.save_live(db, p, draft_id, body)


@router.post(
    "/v1/drafts/{draft_id}/suggestions", response_model=SuggestionOut, status_code=status.HTTP_201_CREATED
)
async def create_suggestion(
    draft_id: uuid.UUID, body: SuggestionCreate, p: CurrentPrincipal, db: DB
) -> SuggestionOut:
    """Ask the AI to edit the selection (or the whole draft). Returns a suggestion everyone sees as a redline;
    nothing changes in the draft until an editor accepts it."""
    return await suggest.create(db, p, draft_id, body)


@router.post("/v1/drafts/{draft_id}/suggestions/{suggestion_id}/accept", response_model=SuggestionOut)
async def accept_suggestion(
    draft_id: uuid.UUID, suggestion_id: uuid.UUID, p: CurrentPrincipal, db: DB
) -> SuggestionOut:
    """Apply it to the shared copy. If its text was changed meanwhile, it comes back `stale` instead."""
    return await suggest.accept(db, p, draft_id, suggestion_id)


@router.post("/v1/drafts/{draft_id}/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
async def reject_suggestion(
    draft_id: uuid.UUID, suggestion_id: uuid.UUID, p: CurrentPrincipal, db: DB
) -> SuggestionOut:
    return await suggest.reject(db, p, draft_id, suggestion_id)


@router.post(
    "/v1/matters/{matter_id}/drafts", response_model=DraftDetail, status_code=status.HTTP_201_CREATED
)
async def create_draft(matter_id: uuid.UUID, body: DraftCreate, p: CurrentPrincipal, db: DB) -> DraftDetail:
    """A blank draft, or one started from an Assistant answer (`from_message_id`)."""
    return await service.create_draft(db, p, matter_id, body)


@router.get("/v1/matters/{matter_id}/drafts", response_model=list[DraftSummary])
async def list_drafts(matter_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> list[DraftSummary]:
    return await service.list_drafts(db, p, matter_id)


@router.get("/v1/drafts/awaiting-me", response_model=list[DraftSummary])
async def awaiting_me(p: CurrentPrincipal, db: DB) -> list[DraftSummary]:
    """Drafts waiting for your approval, in every matter you can open."""
    return await service.awaiting_me(db, p)


@router.get("/v1/drafts/{draft_id}", response_model=DraftDetail)
async def get_draft(draft_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> DraftDetail:
    """Current text, every version, approval history, comments, who is here, recent activity."""
    return await service.detail(db, p, draft_id)


@router.patch("/v1/drafts/{draft_id}", response_model=DraftDetail)
async def update_draft(draft_id: uuid.UUID, body: DraftUpdate, p: CurrentPrincipal, db: DB) -> DraftDetail:
    return await service.update_meta(db, p, draft_id, body)


@router.post("/v1/drafts/{draft_id}/versions", response_model=VersionOut, status_code=status.HTTP_201_CREATED)
async def save_version(draft_id: uuid.UUID, body: VersionCreate, p: CurrentPrincipal, db: DB) -> VersionOut:
    """Save the text as a new version. 409 if someone saved after `base_version_no` (unless `force`)."""
    return await service.save_version(db, p, draft_id, body)


@router.get("/v1/drafts/{draft_id}/versions/{version_no}", response_model=VersionContent)
async def get_version(draft_id: uuid.UUID, version_no: int, p: CurrentPrincipal, db: DB) -> VersionContent:
    return await service.get_version(db, p, draft_id, version_no)


@router.post(
    "/v1/drafts/{draft_id}/versions/{version_no}/restore",
    response_model=VersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def restore_version(draft_id: uuid.UUID, version_no: int, p: CurrentPrincipal, db: DB) -> VersionOut:
    """Copy an old version forward as the newest one; nothing is overwritten."""
    return await service.restore(db, p, draft_id, version_no)


@router.get("/v1/drafts/{draft_id}/diff", response_model=DiffOut)
async def diff(
    draft_id: uuid.UUID,
    p: CurrentPrincipal,
    db: DB,
    from_version: Annotated[int, Query(alias="from", ge=1)],
    to_version: Annotated[int, Query(alias="to", ge=1)],
) -> DiffOut:
    """Word-level changes between two versions."""
    return await service.diff(db, p, draft_id, from_version, to_version)


@router.post("/v1/drafts/{draft_id}/review", response_model=DraftDetail)
async def request_review(
    draft_id: uuid.UUID, body: ReviewRequest, p: CurrentPrincipal, db: DB
) -> DraftDetail:
    """Ask a lead or partner on the matter to approve the current version. Locks editing."""
    return await service.request_review(db, p, draft_id, body)


@router.post("/v1/drafts/{draft_id}/review/decision", response_model=DraftDetail)
async def decide(draft_id: uuid.UUID, body: ReviewDecision, p: CurrentPrincipal, db: DB) -> DraftDetail:
    """The chosen approver approves the version, or requests changes (a note is required)."""
    return await service.decide(db, p, draft_id, body)


@router.post("/v1/drafts/{draft_id}/review/withdraw", response_model=DraftDetail)
async def withdraw(draft_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> DraftDetail:
    return await service.withdraw(db, p, draft_id)


@router.post(
    "/v1/drafts/{draft_id}/comments", response_model=DraftDetail, status_code=status.HTTP_201_CREATED
)
async def add_comment(draft_id: uuid.UUID, body: CommentCreate, p: CurrentPrincipal, db: DB) -> DraftDetail:
    return await service.add_comment(db, p, draft_id, body)


@router.post("/v1/drafts/{draft_id}/comments/{comment_id}/resolve", response_model=DraftDetail)
async def resolve_comment(
    draft_id: uuid.UUID, comment_id: uuid.UUID, p: CurrentPrincipal, db: DB
) -> DraftDetail:
    return await service.resolve_comment(db, p, draft_id, comment_id)


@router.post("/v1/drafts/{draft_id}/presence", response_model=Head)
async def heartbeat(draft_id: uuid.UUID, body: PresenceIn, p: CurrentPrincipal, db: DB) -> Head:
    """Call every few seconds while the draft is open: returns the newest version and who else is here."""
    return await service.heartbeat(db, p, draft_id, body.editing)


@router.get("/v1/drafts/{draft_id}/export")
async def export(
    draft_id: uuid.UUID, p: CurrentPrincipal, db: DB, version: Annotated[int | None, Query(ge=1)] = None
) -> Response:
    """A version as Word, stamped approved or not approved."""
    data, filename = await service.export_docx(db, p, draft_id, version)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
