"""Request dependencies: the principal (bearer token, or a WorkflowHub platform caller), and a firm-scoped DB
transaction."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Unauthorised
from app.core.platform import (
    identity_from_platform_token,
    identity_from_wfh_user,
    principal_for_platform_user,
)
from app.core.security import Principal, principal_from_token
from app.db.session import tenant_session

_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_wfh_user: Annotated[str | None, Header(include_in_schema=False)] = None,
    x_auth_token: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> Principal:
    # 1. Another app or an AI agent, forwarded by WorkflowHub (signed by the platform): see app/core/platform.py
    if x_wfh_user:
        return await principal_for_platform_user(identity_from_wfh_user(x_wfh_user))
    # 2. Our own token: the console, the dev identities, OIDC
    if creds is not None:
        return principal_from_token(creds.credentials)
    # 3. A script calling with a person's platform token
    if x_auth_token:
        return await principal_for_platform_user(await identity_from_platform_token(x_auth_token))
    raise Unauthorised("Missing bearer token")


async def get_db(principal: Annotated[Principal, Depends(get_principal)]) -> AsyncIterator[AsyncSession]:
    async with tenant_session(principal.firm_id) as session:
        yield session


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DB = Annotated[AsyncSession, Depends(get_db)]
