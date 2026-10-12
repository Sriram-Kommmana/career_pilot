"""Password hashing and access tokens.

Passwords are hashed with Argon2id via ``pwdlib``. Plaintext is accepted as a
function argument and never stored, logged, cached, or sent to a model.

``pwdlib`` is used in preference to ``passlib``, which is unmaintained and breaks
against bcrypt 5.x.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from app.core.errors import AuthenticationError

_password_hash: Final = PasswordHash.recommended()

# Token type claim. Only "access" is issued in Phase 1; refresh tokens would use a
# distinct value so one can never be swapped for the other.
TOKEN_TYPE_ACCESS: Final = "access"  # noqa: S105 - a JWT claim value, not a secret

MIN_PASSWORD_LENGTH: Final = 10
MAX_PASSWORD_LENGTH: Final = 256  # bound the work an attacker can make us do


def hash_password(plaintext: str) -> str:
    return _password_hash.hash(plaintext)


def verify_password(plaintext: str, stored_hash: str) -> bool:
    """Return whether the password matches. Never raises on a bad hash."""
    try:
        return _password_hash.verify(plaintext, stored_hash)
    except Exception:  # a malformed stored hash must not become a 500
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with outdated parameters."""
    try:
        return _password_hash.verify_and_update("", stored_hash)[1] is not None
    except Exception:
        return False


def create_access_token(
    *,
    user_id: UUID,
    secret_key: str,
    algorithm: str = "HS256",
    ttl_minutes: int = 60,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint an access token. Returns the token and its expiry.

    The subject is the user id. Authorisation is *not* encoded in the token: every
    request re-checks ownership against the database, so a stolen token cannot carry
    stale permissions.
    """
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "typ": TOKEN_TYPE_ACCESS,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, secret_key, algorithm=algorithm)
    return token, expires_at


def decode_access_token(
    token: str,
    *,
    secret_key: str,
    algorithm: str = "HS256",
) -> UUID:
    """Validate an access token and return the user id it belongs to.

    Raises :class:`AuthenticationError` for anything that is not a currently valid
    access token. The error message is deliberately uniform so it cannot be used to
    probe which part of a token is wrong.
    """
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired.", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid access token.") from exc

    if payload.get("typ") != TOKEN_TYPE_ACCESS:
        raise AuthenticationError("Invalid access token.")

    try:
        return UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid access token.") from exc


def validate_password_strength(plaintext: str) -> None:
    """Enforce a minimum bar. Raises :class:`ValueError` with a usable message."""
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(plaintext) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if plaintext.strip() == "":
        raise ValueError("Password must not be only whitespace.")
