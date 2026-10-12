"""Registration and sign-in.

Two details worth noting:

* Sign-in failures are uniform. A wrong email, a wrong password, and a deactivated
  account all produce the same 401 with the same message, so the endpoint cannot be
  used to enumerate who has an account.
* A verification is still performed when the email is unknown, against a throwaway
  hash. Skipping it would make "no such user" measurably faster than "wrong password",
  which is the same disclosure by a different route.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.models import Profile, User

logger = get_logger(__name__)

# A real Argon2 hash of a value nobody can supply, used to spend comparable time on a
# login attempt for an address that does not exist.
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(uuid.uuid4().hex)
    return _DUMMY_HASH


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Create a user and their empty profile.

    The profile row is created here rather than lazily, so every authenticated request
    can rely on it existing and no endpoint needs a "profile might be missing" branch.
    """
    try:
        validate_password_strength(password)
    except ValueError as exc:
        raise ValidationError(str(exc), details={"field": "password"}) from exc

    normalised_email = email.strip().lower()

    user = User(
        email=normalised_email,
        password_hash=hash_password(password),
        is_active=True,
    )
    user.profile = Profile(full_name=full_name)
    session.add(user)

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "An account with that email address already exists.",
            code="EMAIL_ALREADY_REGISTERED",
        ) from exc

    logger.info("user_registered", user_id=str(user.id))
    return user


async def authenticate_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
) -> User:
    """Verify credentials and return the user. Raises on any failure."""
    normalised_email = email.strip().lower()
    result = await session.execute(select(User).where(User.email == normalised_email))
    user = result.scalar_one_or_none()

    if user is None:
        # Spend the same work as a real verification before failing.
        verify_password(password, _dummy_hash())
        logger.info("login_failed", reason="unknown_email")
        raise AuthenticationError("Incorrect email or password.")

    if not verify_password(password, user.password_hash):
        logger.info("login_failed", reason="bad_password", user_id=str(user.id))
        raise AuthenticationError("Incorrect email or password.")

    if not user.is_active:
        logger.info("login_failed", reason="inactive", user_id=str(user.id))
        raise AuthenticationError("Incorrect email or password.")

    user.last_login_at = utcnow()
    await session.flush()
    logger.info("login_succeeded", user_id=str(user.id))
    return user


def issue_token(user: User, *, settings: Settings) -> tuple[str, object]:
    """Mint an access token for a user who has just been authenticated."""
    token, expires_at = create_access_token(
        user_id=user.id,
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
        ttl_minutes=settings.access_token_ttl_minutes,
    )
    return token, expires_at


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
