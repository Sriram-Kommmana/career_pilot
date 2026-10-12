"""Request dependencies.

``current_user`` is the only way a handler obtains an identity, and it always ends in
a database lookup. Authorisation is deliberately *not* encoded in the token: a token
carries a subject and an expiry, nothing else, so a still-valid token cannot keep
granting access after an account is deactivated.

Ownership is never inferred from a path parameter. Services take ``user_id`` and scope
every query by it, which is why a handler cannot forget the check.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.security import decode_access_token
from app.db.session import get_db_session
from app.models import User
from app.services.auth import get_user_by_id

# auto_error=False so a missing header produces our own error envelope rather than
# FastAPI's default shape.
_bearer = HTTPBearer(auto_error=False, description="Bearer access token.")


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_db_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def current_user(
    session: DbSession,
    settings: AppSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """The authenticated user, or raise 401.

    The token is validated, then the user is loaded and re-checked for being active.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication required.")

    user_id = decode_access_token(
        credentials.credentials,
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Authentication required.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def current_user_id(user: CurrentUser) -> uuid.UUID:
    return user.id


CurrentUserId = Annotated[uuid.UUID, Depends(current_user_id)]


def request_audit_context(request: Request) -> dict[str, object]:
    """Minimal request provenance stored alongside an approval.

    The client IP and user agent, nothing else. No headers are copied wholesale,
    because ``Authorization`` and ``Cookie`` must never reach a database row.
    """
    return {
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:300],
    }


AuditContext = Annotated[dict[str, object], Depends(request_audit_context)]
