"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, session: DbSession, settings: AppSettings
) -> TokenResponse:
    """Create an account and return an access token.

    An empty profile is created at the same time, so every authenticated request can
    rely on one existing.
    """
    user = await auth_service.register_user(
        session,
        email=str(payload.email),
        password=payload.password,
        full_name=payload.full_name,
    )
    token, expires_at = auth_service.issue_token(user, settings=settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: DbSession, settings: AppSettings) -> TokenResponse:
    """Exchange credentials for an access token.

    Every failure mode returns the same 401 with the same message, so this endpoint
    cannot be used to discover which addresses have accounts.
    """
    user = await auth_service.authenticate_user(
        session, email=str(payload.email), password=payload.password
    )
    token, expires_at = auth_service.issue_token(user, settings=settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
