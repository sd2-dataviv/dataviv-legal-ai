"""Request dependencies: principal from bearer token, and a firm-scoped DB transaction."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Unauthorised
from app.core.security import Principal, principal_from_token
from app.db.session import tenant_session

_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None:
        raise Unauthorised("Missing bearer token")
    return principal_from_token(creds.credentials)


async def get_db(principal: Annotated[Principal, Depends(get_principal)]) -> AsyncIterator[AsyncSession]:
    async with tenant_session(principal.firm_id) as session:
        yield session


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
DB = Annotated[AsyncSession, Depends(get_db)]
