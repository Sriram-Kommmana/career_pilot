"""Authentication contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.schemas.common import ApiModel, ApiRequest


class RegisterRequest(ApiRequest):
    email: EmailStr
    password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)]
    full_name: Annotated[str | None, Field(max_length=200)] = None

    @field_validator("password")
    @classmethod
    def _not_only_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password must not be only whitespace.")
        return value


class LoginRequest(ApiRequest):
    email: EmailStr
    # No length bound on login: rejecting a short password here would tell an attacker
    # that the stored one is longer than what they tried.
    password: str


class TokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth2 scheme name, not a credential
    expires_at: datetime


class UserOut(ApiModel):
    id: uuid.UUID
    email: EmailStr
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None
