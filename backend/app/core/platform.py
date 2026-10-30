"""Calls from WorkflowHub, the hosting platform: other apps and AI agents using the shared API.

node.yaml lists the shared endpoints under `api:`. The platform forwards each call as the person asking,
never as the app's owner, and adds X-Wfh-User: an HS256 JWT signed with this app's WFH_APP_API_SECRET
(issuer "workflowhub", audience WFH_APP_API_AUDIENCE) naming that person. A script may instead send the
person's platform token as X-Auth-Token, which the platform's auth service vouches for.

Either way the caller becomes the Dataviv user with the same e-mail, inside that user's firm and with that
user's role, so row-level security, matter membership and ethical walls apply exactly as in the console.
Tokens and signed headers are never logged.
"""

import hashlib
import logging
import threading
import time
import uuid
from typing import cast

import httpx
import jwt
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorised, Unavailable
from app.core.security import Principal, Role
from app.db.session import get_engine

log = logging.getLogger("dataviv.platform")

# Tests swap in an httpx.MockTransport; None = the real network.
transport: httpx.AsyncBaseTransport | None = None

_cache: dict[str, tuple[float, dict[str, str]]] = {}
_lock = threading.Lock()
CACHE_SECONDS = 60


def identity_from_wfh_user(header: str) -> dict[str, str]:
    """Check the platform's signed X-Wfh-User header; returns {"sub", "email", "name"}."""
    s = get_settings()
    if s.wfh_app_api_secret is None:
        raise Unauthorised("The shared API is not enabled here (WFH_APP_API_SECRET is not set)")
    try:
        claims = jwt.decode(
            header,
            s.wfh_app_api_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=s.wfh_app_api_audience,
            issuer="workflowhub",
            options={"verify_aud": bool(s.wfh_app_api_audience), "require": ["sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        log.warning("[platform] X-Wfh-User rejected: %s", type(exc).__name__)
        raise Unauthorised("Invalid X-Wfh-User") from exc
    return {
        "sub": str(claims["sub"]),
        "email": str(claims.get("email") or ""),
        "name": str(claims.get("name") or ""),
    }


async def identity_from_platform_token(token: str) -> dict[str, str]:
    """Ask the platform's auth service who owns `token` (cached for a minute)."""
    s = get_settings()
    if not s.wfh_auth_service_url:
        raise Unauthorised("Platform sign-in is not available here (WFH_AUTH_SERVICE_URL is not set)")
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    url = s.wfh_auth_service_url.rstrip("/") + "/tokens/details"
    try:
        async with httpx.AsyncClient(transport=transport, timeout=8) as client:
            r = await client.get(url, headers={"X-Auth-Token": token})
    except httpx.HTTPError as exc:
        log.warning("[platform] auth service unreachable: %s", type(exc).__name__)
        raise Unavailable("The platform's auth service is not reachable") from exc
    if r.status_code != 200:
        log.warning("[platform] platform token rejected by the auth service (%s)", r.status_code)
        raise Unauthorised("Invalid or expired platform session")
    data = r.json() or {}
    if not data.get("sub"):
        raise Unauthorised("Invalid or expired platform session")
    info = {
        "sub": str(data["sub"]),
        "email": str(data.get("email") or ""),
        "name": str(data.get("full_name") or ""),
    }
    with _lock:
        if len(_cache) > 1000:
            _cache.clear()
        _cache[key] = (now + CACHE_SECONDS, info)
    return info


async def _users_by_email(email: str) -> list[tuple[uuid.UUID, uuid.UUID, str, str]]:
    async with get_engine().connect() as conn:
        rows = await conn.execute(
            text("SELECT user_id, firm_id, role, email FROM platform_user_by_email(:e)"), {"e": email}
        )
        return [(r[0], r[1], r[2], r[3]) for r in rows]


async def principal_for_platform_user(info: dict[str, str]) -> Principal:
    """The Dataviv user a platform caller acts as: same e-mail, in exactly one firm."""
    email = info.get("email", "").strip()
    rows = await _users_by_email(email) if email else []
    if len(rows) > 1:
        raise Forbidden(f"{email} has Dataviv accounts in more than one firm, so the caller is ambiguous")
    if not rows:
        s = get_settings()
        fallback = s.platform_fallback_email
        if s.dev_auth_enabled and fallback:
            rows = (await _users_by_email(fallback))[:1]
        if not rows:
            raise Forbidden(
                f"No Dataviv account for {email or 'this platform user'}: ask a firm admin to add one"
            )
        log.info(
            "[platform] caller %s has no Dataviv account: acting as demo user %s", info.get("sub"), rows[0][3]
        )
    user_id, firm_id, role, user_email = rows[0]
    log.info("[platform] shared API call by platform user %s as %s", info.get("sub"), user_email)
    return Principal(user_id=user_id, firm_id=firm_id, role=cast(Role, role), email=user_email)
