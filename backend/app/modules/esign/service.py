"""E-signature of approved drafts (Step 2e, ADR-021; DRF-12..15).

Flow: a matter lead or partner sends the draft's APPROVED version to the signing provider with one or
more signers in routing order. The provider e-mails each signer in turn. Status reaches us three ways:
a DocuSign Connect callback (when a public URL is configured), a background sweep every
``esign_poll_seconds``, or someone pressing Refresh. When the envelope completes, the signed PDF and the
certificate of completion are stored in the matter's Vault (ingested and searchable like any upload).

Who may do what (matter roles; walls and other firms get 404 like every draft endpoint):

| Action                         | viewer | member | lead / partner |
|--------------------------------|:------:|:------:|:--------------:|
| see envelopes and their status |   ✓    |   ✓    |       ✓        |
| refresh status                 |   ✓    |   ✓    |       ✓        |
| send for signature, void       |        |        |       ✓        |
"""

import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import esign as provider
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound, Unauthorised, Unavailable, Unprocessable
from app.core.esign import DocumentIn, ESignBackend, ESignError, FakeESign
from app.core.models import User
from app.core.permissions import MANAGER_ROLES
from app.core.security import Principal
from app.db.session import get_engine, tenant_session
from app.modules.drafts import service as drafts
from app.modules.drafts.models import Draft
from app.modules.drafts.schemas import Person, SignatureOut, SignerOut
from app.modules.esign.models import SignatureRequest, SignatureSigner
from app.modules.esign.schemas import ESignStatus, FakeAct, SignatureSend, SignatureVoid
from app.modules.vault.service import store_generated

log = logging.getLogger("dataviv.esign")

OPEN = ("sent", "delivered")
EXCLUDED = (
    "Indian law (Information Technology Act 2000, First Schedule) does not allow these to be signed "
    "electronically: negotiable instruments other than cheques, powers of attorney, trust deeds, wills, "
    "and contracts for the sale or conveyance of immovable property."
)


def _backend() -> ESignBackend:
    try:
        b = provider.backend()
    except ESignError as exc:
        raise Unavailable(exc.message) from exc
    if b is None:
        raise Unavailable("E-signature is not switched on for this server (DATAVIV_ESIGN_BACKEND)")
    return b


def _refused(exc: ESignError) -> Exception:
    msg = exc.message + (f" Consent link: {exc.consent_url}" if exc.consent_url else "")
    return Unprocessable(msg) if exc.fixable else Unavailable(msg)


async def status() -> ESignStatus:
    s = get_settings()
    if s.esign_backend == "none":
        return ESignStatus(
            backend="none",
            ready=False,
            detail="E-signature is switched off. Set DATAVIV_ESIGN_BACKEND=docusign (or fake for test mode).",
        )
    try:
        b = provider.backend()
        assert b is not None
        info = await b.account()
    except ESignError as exc:
        return ESignStatus(
            backend=s.esign_backend, ready=False, detail=exc.message, consent_url=exc.consent_url
        )
    return ESignStatus(
        backend=s.esign_backend,
        ready=True,
        environment=info.get("environment"),
        account_name=info.get("account_name"),
        callbacks=bool(s.esign_backend == "docusign" and s.docusign_webhook_url),
    )


# --------------------------------------------------------------------------- reading


async def _outs(db: AsyncSession, reqs: list[SignatureRequest]) -> list[SignatureOut]:
    if not reqs:
        return []
    signers = (
        await db.scalars(
            select(SignatureSigner)
            .where(SignatureSigner.request_id.in_([r.id for r in reqs]))
            .order_by(SignatureSigner.routing_order, SignatureSigner.recipient_id)
        )
    ).all()
    ids = {r.sent_by for r in reqs} | {x.user_id for x in signers if x.user_id}
    names = {r[0]: r[1] for r in (await db.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()}
    by_request: dict[uuid.UUID, list[SignatureSigner]] = {}
    for x in signers:
        by_request.setdefault(x.request_id, []).append(x)

    def person(uid: uuid.UUID) -> Person:
        return Person(id=uid, name=names.get(uid, "Unknown user"))

    return [
        SignatureOut(
            id=r.id,
            draft_id=r.draft_id,
            version_no=r.version_no,
            provider=cast(Any, r.provider),
            envelope_id=r.envelope_id,
            status=cast(Any, r.status),
            subject=r.subject,
            message=r.message,
            sent_by=person(r.sent_by),
            sent_at=r.sent_at,
            completed_at=r.completed_at,
            voided_reason=r.voided_reason,
            last_checked_at=r.last_checked_at,
            last_error=r.last_error,
            signed_document_id=r.signed_document_id,
            certificate_document_id=r.certificate_document_id,
            signers=[
                SignerOut(
                    name=x.name,
                    email=x.email,
                    routing_order=x.routing_order,
                    status=cast(Any, x.status),
                    signed_at=x.signed_at,
                    declined_reason=x.declined_reason,
                    user=person(x.user_id) if x.user_id else None,
                )
                for x in by_request.get(r.id, [])
            ],
        )
        for r in reqs
    ]


async def for_draft(db: AsyncSession, draft_id: uuid.UUID) -> list[SignatureOut]:
    """Envelopes of a draft, newest first. The caller has already checked access to the draft."""
    reqs = (
        await db.scalars(
            select(SignatureRequest)
            .where(SignatureRequest.draft_id == draft_id)
            .order_by(SignatureRequest.sent_at.desc())
        )
    ).all()
    return await _outs(db, list(reqs))


async def list_for_draft(db: AsyncSession, p: Principal, draft_id: uuid.UUID) -> list[SignatureOut]:
    draft, _ = await drafts._draft(db, p, draft_id)
    return await for_draft(db, draft.id)


async def _request(
    db: AsyncSession, p: Principal, request_id: uuid.UUID, *, lock: bool = False
) -> tuple[SignatureRequest, Draft, str]:
    stmt = select(SignatureRequest).where(SignatureRequest.id == request_id)
    req = await db.scalar(stmt.with_for_update() if lock else stmt)
    if req is None:
        raise NotFound("Signature request not found")
    try:
        draft, role = await drafts._draft(db, p, req.draft_id)
    except NotFound:
        raise NotFound("Signature request not found") from None
    return req, draft, role


# --------------------------------------------------------------------------- sending


async def send(db: AsyncSession, p: Principal, draft_id: uuid.UUID, body: SignatureSend) -> SignatureOut:
    draft, role = await drafts._draft(db, p, draft_id, lock=True)
    if role not in MANAGER_ROLES:
        raise Forbidden("Only the matter lead or a partner on the matter can send a draft for signature")
    if not draft.approved_version_no:
        raise Conflict(
            "Only an approved draft can be sent for signature. Ask a lead or partner to approve it first."
        )
    if draft.status == "in_review":
        raise Conflict("A newer version of this draft is waiting for approval. Send it after the decision.")
    if not body.confirm_not_excluded:
        raise Unprocessable(EXCLUDED + " Confirm that this draft is none of these to send it.")
    emails = [x.email.lower() for x in body.signers]
    if len(set(emails)) != len(emails):
        raise Unprocessable("Each signer needs a different e-mail address")
    busy = await db.scalar(
        select(SignatureRequest.id).where(
            SignatureRequest.draft_id == draft.id, SignatureRequest.status.in_(OPEN)
        )
    )
    if busy:
        raise Conflict("This draft is already out for signature. Void that envelope first to send it again.")
    b = _backend()

    # Firm members among the signers are linked to their user (RLS limits the lookup to this firm)
    rows = await db.execute(select(func.lower(User.email), User.id).where(func.lower(User.email).in_(emails)))
    members: dict[str, uuid.UUID] = {r[0]: r[1] for r in rows.all()}
    ordered = sorted(enumerate(body.signers), key=lambda t: (t[1].routing_order, t[0]))
    signers = [
        provider.SignerIn(recipient_id=str(i), name=x.name, email=x.email, routing_order=x.routing_order)
        for i, (_, x) in enumerate(ordered, start=1)
    ]
    n = draft.approved_version_no
    data, filename, plain = await drafts.version_docx(
        db,
        draft,
        n,
        p.user_id,
        signatures=[
            (f"{x.name} ({x.email})", provider.sign_anchor(i), provider.date_anchor(i))
            for i, x in enumerate(signers, start=1)
        ],
    )
    subject = (body.subject or "").strip() or f"Please sign: {draft.title} (version {n})"
    message = (body.message or "").strip() or None
    try:
        envelope_id = await b.create_envelope(
            subject, message, DocumentIn(filename, data, "docx", plain), signers
        )
    except ESignError as exc:
        raise _refused(exc) from exc

    req = SignatureRequest(
        firm_id=p.firm_id,
        matter_id=draft.matter_id,
        draft_id=draft.id,
        version_no=n,
        provider=b.name,
        envelope_id=envelope_id,
        status="sent",
        subject=subject,
        message=message,
        sent_by=p.user_id,
        last_checked_at=datetime.now(UTC),
    )
    db.add(req)
    await db.flush()
    for x in signers:
        db.add(
            SignatureSigner(
                firm_id=p.firm_id,
                request_id=req.id,
                recipient_id=x.recipient_id,
                name=x.name,
                email=x.email,
                routing_order=x.routing_order,
                user_id=members.get(x.email.lower()),
                status="sent" if x.routing_order == signers[0].routing_order else "created",
            )
        )
    await db.flush()
    await drafts._audit(
        db,
        p,
        "draft.signature_sent",
        draft,
        {
            "request_id": str(req.id),
            "version_no": n,
            "provider": b.name,
            "envelope_id": envelope_id,
            "signers": [{"name": x.name, "email": x.email, "order": x.routing_order} for x in signers],
        },
    )
    drafts._changed(db, draft, p, "signature")
    return (await _outs(db, [req]))[0]


# --------------------------------------------------------------------------- status


async def _sync(db: AsyncSession, req: SignatureRequest, actor: Principal) -> None:
    """Bring a request up to date with the provider; store the signed copy once it is complete."""
    now = datetime.now(UTC)
    b = _backend()
    if b.name != req.provider:
        req.last_error = f"This envelope was sent with {req.provider}, but the server now uses {b.name}"
        req.last_checked_at = now
        return
    try:
        st = await b.get_envelope(req.envelope_id)
    except ESignError as exc:
        req.last_error, req.last_checked_at = exc.message, now
        return
    req.last_checked_at, req.last_error = now, None

    signers = {
        x.recipient_id: x
        for x in (await db.scalars(select(SignatureSigner).where(SignatureSigner.request_id == req.id))).all()
    }
    for s in st.signers:
        row = signers.get(s.recipient_id)
        if row is not None:
            row.status, row.signed_at, row.declined_reason = s.status, s.signed_at, s.declined_reason

    draft = await db.get(Draft, req.draft_id)
    assert draft is not None
    before = req.status
    new = st.status
    if new == "completed" and req.signed_document_id is None:
        try:
            signed, cert = await b.download(req.envelope_id)
        except ESignError as exc:
            req.last_error = f"Signed, but the signed copy could not be fetched yet: {exc.message}"
            new = "delivered"  # try again on the next check
        else:
            base = f"{draft.title} v{req.version_no}"
            req.signed_document_id = await store_generated(
                db, actor, req.matter_id, f"{base} - signed.pdf", signed, "esign"
            )
            req.certificate_document_id = await store_generated(
                db, actor, req.matter_id, f"{base} - signing certificate.pdf", cert, "esign"
            )
            req.completed_at = st.completed_at or now
    if new == "voided" and st.voided_reason and not req.voided_reason:
        req.voided_reason = st.voided_reason
    req.status = new
    req.updated_at = now
    await db.flush()
    if new != before:
        action = {
            "completed": "draft.signed",
            "declined": "draft.signature_declined",
            "voided": "draft.signature_voided",
        }.get(new)
        if action:
            declined = next((s for s in st.signers if s.status == "declined"), None)
            await drafts._audit(
                db,
                actor,
                action,
                draft,
                {
                    "request_id": str(req.id),
                    "version_no": req.version_no,
                    "via": req.provider,
                    "reason": (declined.declined_reason if declined else req.voided_reason),
                    "signed_document_id": str(req.signed_document_id) if req.signed_document_id else None,
                },
            )
        drafts._changed(db, draft, actor, "signature")


async def refresh(db: AsyncSession, p: Principal, request_id: uuid.UUID) -> SignatureOut:
    req, _, _ = await _request(db, p, request_id, lock=True)
    if req.status in OPEN:
        await _sync(db, req, p)
    return (await _outs(db, [req]))[0]


async def void(db: AsyncSession, p: Principal, request_id: uuid.UUID, body: SignatureVoid) -> SignatureOut:
    req, draft, role = await _request(db, p, request_id, lock=True)
    if role not in MANAGER_ROLES:
        raise Forbidden("Only the matter lead or a partner on the matter can void an envelope")
    if req.status not in OPEN:
        raise Conflict(f"This envelope is {req.status}; only one still out for signature can be voided")
    b = _backend()
    try:
        await b.void(req.envelope_id, body.reason.strip())
    except ESignError as exc:
        raise _refused(exc) from exc
    req.status, req.voided_reason, req.updated_at = "voided", body.reason.strip(), datetime.now(UTC)
    for x in (await db.scalars(select(SignatureSigner).where(SignatureSigner.request_id == req.id))).all():
        if x.status not in ("signed", "declined"):
            x.status = "voided"
    await db.flush()
    await drafts._audit(
        db,
        p,
        "draft.signature_voided",
        draft,
        {"request_id": str(req.id), "version_no": req.version_no, "reason": req.voided_reason},
    )
    drafts._changed(db, draft, p, "signature")
    return (await _outs(db, [req]))[0]


async def simulate(db: AsyncSession, p: Principal, request_id: uuid.UUID, body: FakeAct) -> SignatureOut:
    """Test mode only: play the signers' part (open, sign, decline), then sync as a callback would."""
    s = get_settings()
    if not s.dev_auth_enabled or s.esign_backend != "fake":
        raise NotFound("Not found")
    req, _, role = await _request(db, p, request_id, lock=True)
    if role not in MANAGER_ROLES | {"member"}:
        raise Forbidden("Viewers cannot simulate signing")
    b = _backend()
    assert isinstance(b, FakeESign)
    try:
        b.act(req.envelope_id, body.action, body.reason)
    except ESignError as exc:
        raise Conflict(exc.message) from exc
    await _sync(db, req, p)
    return (await _outs(db, [req]))[0]


# --------------------------------------------------------------------------- callbacks and the sweep


async def _sender(db: AsyncSession, req: SignatureRequest) -> Principal:
    """Changes found by a callback or the sweep are recorded on behalf of the person who sent the envelope."""
    u = await db.get(User, req.sent_by)
    assert u is not None
    return Principal(user_id=u.id, firm_id=u.firm_id, role=cast(Any, u.role), email=u.email)


async def _sync_by_id(request_id: uuid.UUID, firm_id: uuid.UUID) -> None:
    async with tenant_session(firm_id) as db:
        req = await db.scalar(
            select(SignatureRequest)
            .where(SignatureRequest.id == request_id)
            .with_for_update(skip_locked=True)
        )
        if req is not None and req.status in OPEN:
            await _sync(db, req, await _sender(db, req))


def verify_connect_signature(body: bytes, signatures: list[str]) -> None:
    """DocuSign Connect HMAC: base64(HMAC-SHA256(key, raw body)) in X-DocuSign-Signature-1..n."""
    s = get_settings()
    secret = s.docusign_connect_secret
    if secret is None:
        if s.dev_auth_enabled:
            return  # dev/test without a key; production must have one
        raise Unauthorised("DocuSign callbacks are refused until DATAVIV_DOCUSIGN_CONNECT_SECRET is set")
    want = base64.b64encode(
        hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).digest()
    ).decode()
    if not any(hmac.compare_digest(want, x.strip()) for x in signatures if x):
        raise Unauthorised("Invalid DocuSign signature")


async def connect_callback(body: bytes, signatures: list[str]) -> bool:
    """Handle a DocuSign Connect event. Returns True when it matched one of our envelopes."""
    verify_connect_signature(body, signatures)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise Unprocessable("Callback body is not JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    envelope_id = (data or {}).get("envelopeId") or (
        payload.get("envelopeId") if isinstance(payload, dict) else None
    )
    if not envelope_id:
        return False
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text("SELECT request_id, firm_id FROM esign_request_firm('docusign', :e)"),
                {"e": str(envelope_id)},
            )
        ).first()
    if row is None:
        log.info("DocuSign callback for unknown envelope %s ignored", envelope_id)
        return False
    await _sync_by_id(row[0], row[1])
    return True


async def sync_open(limit: int = 20) -> int:
    """Check envelopes still out for signature that nobody has checked recently. Used by the worker."""
    s = get_settings()
    if s.esign_backend == "none":
        return 0
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT request_id, firm_id FROM esign_open_requests(:stale, :n)"),
                {"stale": timedelta(seconds=s.esign_poll_seconds), "n": limit},
            )
        ).all()
    for request_id, firm_id in rows:
        try:
            await _sync_by_id(request_id, firm_id)
        except Exception:
            log.exception("checking signature request %s failed", request_id)
    return len(rows)
