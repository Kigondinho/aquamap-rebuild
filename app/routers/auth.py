from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.deps import CurrentUserDep, DbSession
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    Resend2FARequest,
    Verify2FARequest,
)
from app.schemas.common import MessageResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> MessageResponse:
    auth_service.register_user(db, payload)
    return MessageResponse(message="Account created. Please sign in.")


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: DbSession, response: Response) -> LoginResponse:
    result, _user = auth_service.start_login(db, payload)
    # Later phase: when no second factor is required, set the session cookie here.
    return result


@router.post("/verify-2fa", response_model=MessageResponse)
def verify_2fa(payload: Verify2FARequest, db: DbSession, response: Response) -> MessageResponse:
    auth_service.verify_second_factor(db, payload)
    # Later phase: set the session cookie here.
    return MessageResponse(message="Authentication successful.")


@router.post("/resend-2fa", response_model=LoginResponse)
def resend_2fa(payload: Resend2FARequest, db: DbSession) -> LoginResponse:
    return auth_service.resend_second_factor(db, payload)


@router.post("/logout", response_model=MessageResponse)
def logout(response: Response) -> MessageResponse:
    # Later phase: clear the session cookie via the configured cookie name.
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUserDep) -> CurrentUser:
    return user
