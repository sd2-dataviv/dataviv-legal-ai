"""Authentication: turns a bearer token into a Principal (FND-02).

Dev/test: HS256 tokens issued by /v1/dev/token.
Staging/prod: OIDC tokens from Keycloak, verified against the issuer's JWKS.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

from app.core.config import get_settings
from app.core.errors import Unauthorised

Role = Literal["partner", "associate", "junior", "paralegal", "admin", "client"]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    firm_id: uuid.UUID
    role: Role
    email: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def issue_dev_token(user_id: uuid.UUID, firm_id: uuid.UUID, role: Role, email: str) -> str:
    s = get_settings()
    if not s.dev_auth_enabled:
        raise Unauthorised("Dev tokens are disabled outside dev/test")
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "firm_id": str(firm_id),
        "role": role,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_ttl_minutes),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


_jwks_client: jwt.PyJWKClient | None = None


def _decode(token: str) -> dict[str, object]:
    s = get_settings()
    if s.dev_auth_enabled:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    if not s.oidc_issuer:
        raise Unauthorised("OIDC issuer not configured")
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{s.oidc_issuer}/protocol/openid-connect/certs")
    key = _jwks_client.get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["RS256"], audience=s.oidc_audience, issuer=s.oidc_issuer)


def principal_from_token(token: str) -> Principal:
    try:
        c = _decode(token)
        return Principal(
            user_id=uuid.UUID(str(c["sub"])),
            firm_id=uuid.UUID(str(c["firm_id"])),
            role=c["role"],  # type: ignore[arg-type]
            email=str(c.get("email", "")),
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise Unauthorised("Invalid or expired token") from exc
