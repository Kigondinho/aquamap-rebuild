from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.deps import CurrentUserDep, DbSession
from app.errors import AuthenticationError
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
    Resend2FARequest,
    Verify2FARequest,
)
from app.schemas.common import MessageResponse, error_responses
from app.services import auth as auth_service
from app.services import sessions
from app.services.rate_limit import login_limiter

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED,
             summary="Create an account", responses=error_responses(409, 422, 503))
def register(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    """Creates a normal user account. It does not sign the user in. Administrators are created from the
    command line (`python -m app.db_tools create-user --admin`), never through this endpoint."""
    user = auth_service.register_user(db, payload)
    return RegisterResponse(message="Account created. Please sign in.", user=user)


@router.post("/login", response_model=LoginResponse, summary="Sign in",
             responses=error_responses(401, 422, 429, 503))
def login(payload: LoginRequest, db: DbSession, request: Request, response: Response) -> LoginResponse:
    """Checks the password and sets the session as an HTTP-only cookie. After 5 failed attempts for the same
    email from the same address within 15 minutes, further attempts answer 429."""
    client = request.client.host if request.client else "unknown"
    key = f"{auth_service.normalise_email(payload.email)}|{client}"
    login_limiter.check(key)
    try:
        result, user = auth_service.start_login(db, payload)
    except AuthenticationError:
        login_limiter.record_failure(key)
        raise
    login_limiter.reset(key)
    if user is not None:
        sessions.set_session_cookie(response, sessions.create_session_token(user.user_id, user.role))
    return result


@router.post("/verify-2fa", response_model=MessageResponse, summary="Verify a one-time code (optional module, not built)",
             responses=error_responses(422, 501))
def verify_2fa(payload: Verify2FARequest, db: DbSession, response: Response) -> MessageResponse:
    auth_service.verify_second_factor(db, payload)
    return MessageResponse(message="Authentication successful.")


@router.post("/resend-2fa", response_model=LoginResponse, summary="Resend a one-time code (optional module, not built)",
             responses=error_responses(422, 501))
def resend_2fa(payload: Resend2FARequest, db: DbSession) -> LoginResponse:
    return auth_service.resend_second_factor(db, payload)


@router.post("/logout", response_model=MessageResponse, summary="Sign out")
def logout(response: Response) -> MessageResponse:
    """Clears the session cookie. Safe to call when not signed in."""
    sessions.clear_session_cookie(response)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=CurrentUser, summary="The signed-in user", responses=error_responses(401, 503))
def me(user: CurrentUserDep) -> CurrentUser:
    return user
