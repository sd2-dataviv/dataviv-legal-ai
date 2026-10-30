"""E-signature endpoints (Step 2e). See service.py for who may do what."""

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import DB, CurrentPrincipal
from app.modules.drafts.schemas import SignatureOut
from app.modules.esign import service
from app.modules.esign.schemas import ESignStatus, FakeAct, SignatureSend, SignatureVoid

router = APIRouter(tags=["e-signature"])


@router.get("/v1/esign/status", response_model=ESignStatus)
async def esign_status(_: CurrentPrincipal) -> ESignStatus:
    """Whether drafts can be sent for signature now, with which provider and account, or what to fix."""
    return await service.status()


@router.post(
    "/v1/drafts/{draft_id}/signatures", response_model=SignatureOut, status_code=status.HTTP_201_CREATED
)
async def send_for_signature(
    draft_id: uuid.UUID, body: SignatureSend, p: CurrentPrincipal, db: DB
) -> SignatureOut:
    """Send the draft's approved version for signature (matter lead or partner)."""
    return await service.send(db, p, draft_id, body)


@router.get("/v1/drafts/{draft_id}/signatures", response_model=list[SignatureOut])
async def list_signatures(draft_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> list[SignatureOut]:
    return await service.list_for_draft(db, p, draft_id)


@router.post("/v1/signatures/{request_id}/refresh", response_model=SignatureOut)
async def refresh_signature(request_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> SignatureOut:
    """Ask the provider for the latest status now (it is also checked in the background)."""
    return await service.refresh(db, p, request_id)


@router.post("/v1/signatures/{request_id}/void", response_model=SignatureOut)
async def void_signature(
    request_id: uuid.UUID, body: SignatureVoid, p: CurrentPrincipal, db: DB
) -> SignatureOut:
    """Cancel an envelope still out for signature (matter lead or partner); signers are told why."""
    return await service.void(db, p, request_id, body)


@router.post("/v1/dev/signatures/{request_id}/simulate", response_model=SignatureOut, include_in_schema=False)
async def simulate_signing(request_id: uuid.UUID, body: FakeAct, p: CurrentPrincipal, db: DB) -> SignatureOut:
    """Test mode only (DATAVIV_ESIGN_BACKEND=fake in dev/test): act as the next signer."""
    return await service.simulate(db, p, request_id, body)


@router.post("/v1/esign/docusign/connect", include_in_schema=False)
async def docusign_connect(request: Request) -> dict[str, bool]:
    """DocuSign Connect callback. Authenticated by its HMAC signature, not a bearer token."""
    body = await request.body()
    sigs = [v for k, v in request.headers.items() if k.lower().startswith("x-docusign-signature-")]
    return {"matched": await service.connect_callback(body, sigs)}
