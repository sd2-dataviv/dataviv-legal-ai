"""E-signature providers (Step 2e, ADR-021).

One small interface, two backends:

* ``DocuSign`` - the DocuSign eSignature REST API v2.1 over ``httpx``, authenticated with the JWT grant
  (an RSA-signed assertion for a DocuSign user who has consented to ``signature impersonation``).
  Signature and date fields are placed with anchor text that the exported Word file carries in white.
* ``FakeESign`` - an offline test mode for development and the test suite: no e-mail is sent, nothing
  leaves the machine, and signing is simulated (``act``). Its state lives in files under the storage
  directory, so the API and a separate worker process see the same envelopes.

The service layer (app/modules/esign) never talks HTTP itself; it only uses ``ESignBackend``.
"""

import base64
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings
from app.core.pdf import text_pdf

log = logging.getLogger("dataviv.esign")

EnvelopeStatus = Literal["sent", "delivered", "completed", "declined", "voided"]
SignerStatus = Literal["created", "sent", "delivered", "signed", "declined", "voided"]
FakeAction = Literal["deliver", "sign_next", "sign_all", "decline"]


class ESignError(Exception):
    """The provider refused or could not be reached. `fixable` = a setup problem the firm can fix."""

    def __init__(self, message: str, *, fixable: bool = False, consent_url: str | None = None) -> None:
        super().__init__(message)
        self.message, self.fixable, self.consent_url = message, fixable, consent_url


def sign_anchor(n: int) -> str:
    """Anchor text where signer n signs (placed in the document in white, so it is invisible)."""
    return f"/dvsig{n}/"


def date_anchor(n: int) -> str:
    return f"/dvdate{n}/"


@dataclass(frozen=True)
class SignerIn:
    recipient_id: str
    name: str
    email: str
    routing_order: int


@dataclass(frozen=True)
class DocumentIn:
    name: str  # file name shown to signers, e.g. "Notice to Lotus Holdings v2.docx"
    data: bytes
    extension: str  # "docx" or "pdf"
    text: str  # plain text of the document (used by the test backend to render its signed copy)


@dataclass
class SignerState:
    recipient_id: str
    status: SignerStatus
    signed_at: datetime | None = None
    declined_reason: str | None = None


@dataclass
class EnvelopeState:
    envelope_id: str
    status: EnvelopeStatus
    signers: list[SignerState] = field(default_factory=list)
    voided_reason: str | None = None
    completed_at: datetime | None = None


class ESignBackend(Protocol):
    @property
    def name(self) -> Literal["docusign", "fake"]: ...

    async def create_envelope(
        self, subject: str, message: str | None, document: DocumentIn, signers: list[SignerIn]
    ) -> str: ...
    async def get_envelope(self, envelope_id: str) -> EnvelopeState: ...
    async def void(self, envelope_id: str, reason: str) -> None: ...
    async def download(self, envelope_id: str) -> tuple[bytes, bytes]:
        """(signed document as PDF, certificate of completion as PDF)"""
        ...

    async def account(self) -> dict[str, Any]: ...


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- DocuSign

_ENVELOPE_STATUS: dict[str, EnvelopeStatus] = {
    "created": "sent",
    "sent": "sent",
    "delivered": "delivered",
    "signed": "delivered",
    "completed": "completed",
    "declined": "declined",
    "voided": "voided",
}
_SIGNER_STATUS: dict[str, SignerStatus] = {
    "created": "created",
    "sent": "sent",
    "delivered": "delivered",
    "signed": "signed",
    "completed": "signed",
    "declined": "declined",
}


class DocuSign:
    name: Literal["docusign"] = "docusign"
    SCOPES = "signature impersonation"

    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        missing = [
            k
            for k, v in (
                ("DATAVIV_DOCUSIGN_INTEGRATION_KEY", s.docusign_integration_key),
                ("DATAVIV_DOCUSIGN_USER_ID", s.docusign_user_id),
                (
                    "DATAVIV_DOCUSIGN_PRIVATE_KEY or DATAVIV_DOCUSIGN_PRIVATE_KEY_PATH",
                    s.docusign_private_key or s.docusign_private_key_path,
                ),
            )
            if not v
        ]
        if missing:
            raise ESignError("DocuSign is not configured: set " + ", ".join(missing), fixable=True)
        self.s = s
        self.auth = s.docusign_auth_server.removeprefix("https://").rstrip("/")
        self._transport = transport
        self._token: tuple[str, float] | None = None
        self._base: tuple[str, str, str] | None = None  # (rest base url, account id, account name)

    def consent_url(self, redirect_uri: str = "https://developers.docusign.com/platform/auth/consent") -> str:
        return (
            f"https://{self.auth}/oauth/auth?response_type=code&scope={quote(self.SCOPES)}"
            f"&client_id={self.s.docusign_integration_key}&redirect_uri={quote(redirect_uri, safe='')}"
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.s.esign_timeout_seconds, transport=self._transport)

    def _private_key(self) -> str:
        if self.s.docusign_private_key is not None:
            return self.s.docusign_private_key.get_secret_value().replace("\\n", "\n")
        path = self.s.docusign_private_key_path
        try:
            return Path(path).read_text(encoding="utf-8") if path else ""
        except OSError as exc:
            raise ESignError(f"Cannot read the DocuSign private key file: {exc}", fixable=True) from exc

    def _assertion(self) -> str:
        import jwt  # pyjwt; RS256 needs the 'cryptography' package

        now = int(time.time())
        claims = {
            "iss": self.s.docusign_integration_key,
            "sub": self.s.docusign_user_id,
            "aud": self.auth,
            "iat": now,
            "exp": now + 3600,
            "scope": self.SCOPES,
        }
        try:
            return jwt.encode(claims, self._private_key(), algorithm="RS256")
        except NotImplementedError as exc:  # pyjwt without cryptography
            raise ESignError(
                "Install the 'cryptography' package to sign DocuSign tokens", fixable=True
            ) from exc
        except (ValueError, TypeError, jwt.PyJWTError) as exc:
            raise ESignError(f"The DocuSign private key is not a valid RSA key: {exc}", fixable=True) from exc

    async def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        async with self._client() as c:
            r = await c.post(
                f"https://{self.auth}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self._assertion(),
                },
            )
        if r.status_code != 200:
            err = _error_text(r)
            if "consent_required" in err:
                raise ESignError(
                    "The DocuSign user has not yet allowed this app to sign in on their behalf. Open the "
                    "consent link once, sign in and accept.",
                    fixable=True,
                    consent_url=self.consent_url(),
                )
            raise ESignError(f"DocuSign sign-in failed: {err}", fixable=r.status_code in (400, 401))
        body = r.json()
        self._token = (body["access_token"], time.time() + float(body.get("expires_in", 3600)))
        return self._token[0]

    async def _account(self) -> tuple[str, str, str]:
        if self._base:
            return self._base
        token = await self._access_token()
        async with self._client() as c:
            r = await c.get(
                f"https://{self.auth}/oauth/userinfo", headers={"Authorization": f"Bearer {token}"}
            )
        if r.status_code != 200:
            raise ESignError(f"DocuSign user info failed: {_error_text(r)}")
        accounts = r.json().get("accounts", [])
        wanted = self.s.docusign_account_id
        acct = next(
            (a for a in accounts if (a["account_id"] == wanted if wanted else a.get("is_default"))),
            None,
        )
        if acct is None:
            raise ESignError(
                f"DocuSign account {wanted} is not available to this user"
                if wanted
                else "The DocuSign user has no default account",
                fixable=True,
            )
        self._base = (
            f"{acct['base_uri']}/restapi/v2.1/accounts/{acct['account_id']}",
            acct["account_id"],
            acct.get("account_name", ""),
        )
        return self._base

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        base, _, _ = await self._account()
        token = await self._access_token()
        async with self._client() as c:
            try:
                r = await c.request(method, base + path, headers={"Authorization": f"Bearer {token}"}, **kw)
            except httpx.HTTPError as exc:
                raise ESignError(f"DocuSign could not be reached: {exc}") from exc
        if r.status_code >= 400:
            raise ESignError(f"DocuSign refused the request: {_error_text(r)}", fixable=r.status_code < 500)
        return r

    async def create_envelope(
        self, subject: str, message: str | None, document: DocumentIn, signers: list[SignerIn]
    ) -> str:
        body: dict[str, Any] = {
            "emailSubject": subject[:100],
            "documents": [
                {
                    "documentBase64": base64.b64encode(document.data).decode(),
                    "name": document.name,
                    "fileExtension": document.extension,
                    "documentId": "1",
                }
            ],
            "recipients": {
                "signers": [
                    {
                        "email": x.email,
                        "name": x.name,
                        "recipientId": x.recipient_id,
                        "routingOrder": str(x.routing_order),
                        "tabs": {
                            "signHereTabs": [
                                {
                                    "anchorString": sign_anchor(n),
                                    "anchorUnits": "pixels",
                                    "anchorXOffset": "0",
                                    "anchorYOffset": "-6",
                                    "anchorIgnoreIfNotPresent": "false",
                                }
                            ],
                            "dateSignedTabs": [
                                {
                                    "anchorString": date_anchor(n),
                                    "anchorUnits": "pixels",
                                    "anchorXOffset": "0",
                                    "anchorYOffset": "-2",
                                    "anchorIgnoreIfNotPresent": "false",
                                }
                            ],
                        },
                    }
                    for n, x in enumerate(signers, start=1)
                ]
            },
            "status": "sent",
        }
        if message:
            body["emailBlurb"] = message[:10000]
        if self.s.docusign_webhook_url:
            body["eventNotification"] = {
                "url": self.s.docusign_webhook_url,
                "requireAcknowledgment": "true",
                "includeHMAC": "true" if self.s.docusign_connect_secret else "false",
                "eventData": {"version": "restv2.1", "format": "json"},
                "envelopeEvents": [
                    {"envelopeEventStatusCode": e}
                    for e in ("sent", "delivered", "completed", "declined", "voided")
                ],
                "recipientEvents": [
                    {"recipientEventStatusCode": e} for e in ("Delivered", "Completed", "Declined")
                ],
            }
        r = await self._call("POST", "/envelopes", json=body)
        return str(r.json()["envelopeId"])

    async def get_envelope(self, envelope_id: str) -> EnvelopeState:
        env = (await self._call("GET", f"/envelopes/{envelope_id}")).json()
        rec = (await self._call("GET", f"/envelopes/{envelope_id}/recipients")).json()
        status = _ENVELOPE_STATUS.get(str(env.get("status", "")).lower(), "sent")
        signers = [
            SignerState(
                recipient_id=str(x["recipientId"]),
                status=_SIGNER_STATUS.get(str(x.get("status", "")).lower(), "sent"),
                signed_at=_when(x.get("signedDateTime")),
                declined_reason=x.get("declinedReason"),
            )
            for x in rec.get("signers", [])
        ]
        if status == "voided":
            for x in signers:
                if x.status not in ("signed", "declined"):
                    x.status = "voided"
        return EnvelopeState(
            envelope_id=envelope_id,
            status=status,
            signers=signers,
            voided_reason=env.get("voidedReason"),
            completed_at=_when(env.get("completedDateTime")),
        )

    async def void(self, envelope_id: str, reason: str) -> None:
        await self._call(
            "PUT", f"/envelopes/{envelope_id}", json={"status": "voided", "voidedReason": reason[:200]}
        )

    async def download(self, envelope_id: str) -> tuple[bytes, bytes]:
        signed = (await self._call("GET", f"/envelopes/{envelope_id}/documents/combined")).content
        cert = (await self._call("GET", f"/envelopes/{envelope_id}/documents/certificate")).content
        return signed, cert

    async def account(self) -> dict[str, Any]:
        _, account_id, account_name = await self._account()
        return {
            "account_id": account_id,
            "account_name": account_name,
            "environment": ("sandbox" if self.auth.startswith("account-d.") else "production"),
        }


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if isinstance(body, dict):
        parts = [
            str(body.get(k)) for k in ("errorCode", "message", "error", "error_description") if body.get(k)
        ]
        if parts:
            return " - ".join(parts)
    return f"HTTP {r.status_code}"


# --------------------------------------------------------------------------- offline test backend


class FakeESign:
    """Envelopes as JSON files; nothing is e-mailed. `act` plays the signers' part."""

    name: Literal["fake"] = "fake"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, envelope_id: str) -> Path:
        uuid.UUID(envelope_id)  # refuses anything that is not an envelope id (no path tricks)
        return self.root / f"{envelope_id}.json"

    def _load(self, envelope_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._path(envelope_id).read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (OSError, ValueError) as exc:
            raise ESignError(f"Envelope {envelope_id} not found in test mode") from exc

    def _store(self, env: dict[str, Any]) -> None:
        self._path(env["envelope_id"]).write_text(json.dumps(env, default=str), encoding="utf-8")

    async def create_envelope(
        self, subject: str, message: str | None, document: DocumentIn, signers: list[SignerIn]
    ) -> str:
        if not signers:
            raise ESignError("At least one signer is needed")
        first = min(x.routing_order for x in signers)
        env = {
            "envelope_id": str(uuid.uuid4()),
            "subject": subject,
            "message": message,
            "document_name": document.name,
            "document_text": document.text,
            "status": "sent",
            "created_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
            "voided_reason": None,
            "signers": [
                {
                    **asdict(x),
                    "status": "sent" if x.routing_order == first else "created",
                    "signed_at": None,
                    "declined_reason": None,
                }
                for x in signers
            ],
        }
        self._store(env)
        return str(env["envelope_id"])

    async def get_envelope(self, envelope_id: str) -> EnvelopeState:
        env = self._load(envelope_id)
        return EnvelopeState(
            envelope_id=envelope_id,
            status=env["status"],
            signers=[
                SignerState(x["recipient_id"], x["status"], _when(x["signed_at"]), x["declined_reason"])
                for x in env["signers"]
            ],
            voided_reason=env["voided_reason"],
            completed_at=_when(env["completed_at"]),
        )

    async def void(self, envelope_id: str, reason: str) -> None:
        env = self._load(envelope_id)
        if env["status"] in ("completed", "declined", "voided"):
            raise ESignError(f"An envelope that is {env['status']} cannot be voided")
        env["status"], env["voided_reason"] = "voided", reason
        for x in env["signers"]:
            if x["status"] not in ("signed", "declined"):
                x["status"] = "voided"
        self._store(env)

    def act(self, envelope_id: str, action: FakeAction, reason: str | None = None) -> None:
        """Simulate what the next signer(s) do: open the e-mail, sign, or decline."""
        env = self._load(envelope_id)
        if env["status"] not in ("sent", "delivered"):
            raise ESignError(f"The envelope is {env['status']}; nothing is waiting for a signer")
        steps = len(env["signers"]) if action == "sign_all" else 1
        for _ in range(steps):
            waiting = sorted(
                (x for x in env["signers"] if x["status"] in ("created", "sent", "delivered")),
                key=lambda x: x["routing_order"],
            )
            if not waiting:
                break
            x = waiting[0]
            now = datetime.now(UTC).isoformat()
            if action == "deliver":
                x["status"], env["status"] = "delivered", "delivered"
            elif action == "decline":
                x["status"], x["declined_reason"] = "declined", reason or "Declined in test mode"
                env["status"] = "declined"
                break
            else:
                x["status"], x["signed_at"] = "signed", now
                env["status"] = "delivered"
                rest = [y for y in env["signers"] if y["status"] in ("created", "sent", "delivered")]
                if rest:
                    nxt = min(y["routing_order"] for y in rest)
                    for y in rest:
                        if y["routing_order"] == nxt and y["status"] == "created":
                            y["status"] = "sent"
                else:
                    env["status"], env["completed_at"] = "completed", now
        self._store(env)

    async def download(self, envelope_id: str) -> tuple[bytes, bytes]:
        env = self._load(envelope_id)
        if env["status"] != "completed":
            raise ESignError("Only a completed envelope has a signed copy")
        lines = env["document_text"].splitlines()
        lines += ["", "SIGNATURES (TEST MODE - NOT LEGALLY SIGNED)"]
        lines += [
            f"Signed by {x['name']} <{x['email']}> on {x['signed_at'][:19].replace('T', ' ')} UTC"
            for x in env["signers"]
        ]
        signed = text_pdf(env["document_name"].rsplit(".", 1)[0] + " (signed, test mode)", lines)
        cert = text_pdf(
            "Certificate of completion (test mode)",
            [
                f"Envelope: {envelope_id}",
                f"Subject: {env['subject']}",
                f"Document: {env['document_name']}",
                f"Sent: {env['created_at'][:19].replace('T', ' ')} UTC",
                f"Completed: {(env['completed_at'] or '')[:19].replace('T', ' ')} UTC",
                "",
                *[
                    f"{x['routing_order']}. {x['name']} <{x['email']}> signed "
                    f"{x['signed_at'][:19].replace('T', ' ')} UTC"
                    for x in env["signers"]
                ],
                "",
                "Produced by the Dataviv AI OS e-signature test backend. No e-mail was sent and this is "
                "not a legal signature.",
            ],
        )
        return signed, cert

    async def account(self) -> dict[str, Any]:
        return {
            "account_id": "test",
            "account_name": "Test mode (no e-mails are sent)",
            "environment": "test",
        }


# --------------------------------------------------------------------------- selection

_docusign: tuple[tuple[Any, ...], DocuSign] | None = None


def backend(transport: httpx.AsyncBaseTransport | None = None) -> ESignBackend | None:
    """The configured backend, or None when e-signature is switched off. DocuSign clients are reused
    (they cache the access token) until the settings change."""
    global _docusign
    s = get_settings()
    if s.esign_backend == "none":
        return None
    if s.esign_backend == "fake":
        return FakeESign(s.storage_dir / "_esign_fake")
    key = (
        s.docusign_integration_key,
        s.docusign_user_id,
        s.docusign_auth_server,
        s.docusign_account_id,
        s.docusign_private_key_path,
        s.docusign_private_key,
        s.docusign_webhook_url,
        transport,
    )
    if _docusign is None or _docusign[0] != key:
        _docusign = (key, DocuSign(s, transport))
    return _docusign[1]
