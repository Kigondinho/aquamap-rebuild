from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.deps import CurrentUserDep, DbSession
from app.errors import AuthenticationError, BadRequestError
from app.schemas.auth import (
    CurrentUser,
    EmailRequest,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    PasswordResetRequest,
    RegisterRequest,
    RegisterResponse,
    Resend2FARequest,
    Verify2FARequest,
    VerifyEmailRequest,
)
from app.schemas.common import MessageResponse, error_responses
from app.services import auth as auth_service
from app.services import sessions
from app.services.rate_limit import login_limiter
from app.settings import get_settings

router = APIRouter(prefix="/auth", tags=["Authentication"])

RESEND_MESSAGE = "If that address has an account waiting for confirmation, a new code has been sent."
FORGOT_MESSAGE = "If that address has an account, a password reset code has been sent."


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED,
             summary="Create an account", responses=error_responses(409, 422, 503))
def register(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    """Creates a normal user account and does not sign in. When the server can send email, a six-digit code
    is emailed and the address must be confirmed (`/auth/verify-email`) before signing in;
    `verification_required` says which applies. Administrators are created from the command line only."""
    user, needs_verification = auth_service.register_user(db, payload)
    message = ("Account created. We emailed you a code: confirm your address to sign in." if needs_verification
               else "Account created. Please sign in.")
    return RegisterResponse(message=message, user=user, verification_required=needs_verification)


@router.post("/verify-email", response_model=MessageResponse, summary="Confirm an email address with its code",
             responses=error_responses(400, 422, 503))
def verify_email(payload: VerifyEmailRequest, db: DbSession) -> MessageResponse:
    """The code is valid for a few minutes, works once, and is cancelled after 5 wrong guesses. Every failure
    gives the same answer (400)."""
    auth_service.verify_email(db, payload.email, payload.code)
    return MessageResponse(message="Email confirmed. You can sign in now.")


@router.post("/resend-verification", response_model=MessageResponse, summary="Email a new verification code",
             responses=error_responses(422, 503))
def resend_verification(payload: EmailRequest, db: DbSession) -> MessageResponse:
    """Always answers the same, whether or not the address has an account. At most one code per minute."""
    auth_service.resend_verification(db, payload.email)
    return MessageResponse(message=RESEND_MESSAGE)


@router.post("/login", response_model=LoginResponse, summary="Sign in",
             responses=error_responses(401, 403, 422, 429, 503))
def login(payload: LoginRequest, db: DbSession, request: Request, response: Response) -> LoginResponse:
    """Checks the password and sets the session as an HTTP-only cookie. 403 with `code: email_not_verified`
    if the address still has to be confirmed. After 5 failed attempts for the same email from the same
    address within 15 minutes, further attempts answer 429."""
    key = f"{auth_service.normalise_email(payload.email)}|{_client(request)}"
    login_limiter.check(key)
    try:
        result, user, version = auth_service.start_login(db, payload)
    except AuthenticationError:
        login_limiter.record_failure(key)
        raise
    login_limiter.reset(key)
    sessions.set_session_cookie(response, sessions.create_session_token(user.user_id, user.role, version))
    return result


@router.post("/logout", response_model=MessageResponse, summary="Sign out")
def logout(db: DbSession, request: Request, response: Response) -> MessageResponse:
    """Ends the session on the server (and every other session of the account), then clears the cookie.
    A copy of the old cookie stops working. Safe to call when not signed in."""
    token = request.cookies.get(get_settings().session_cookie_name)
    if token:
        try:
            claims = sessions.decode_session(token)
            auth_service.get_active_user(db, claims.user_id, claims.version)  # only a live session may end sessions
            auth_service.end_sessions(db, claims.user_id)
        except AuthenticationError:
            pass
    sessions.clear_session_cookie(response)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=CurrentUser, summary="The signed-in user", responses=error_responses(401, 503))
def me(user: CurrentUserDep) -> CurrentUser:
    return user


@router.post("/password/change", response_model=MessageResponse, summary="Change the password of the signed-in user",
             responses=error_responses(400, 401, 422, 429, 503))
def change_password(payload: PasswordChangeRequest, user: CurrentUserDep, db: DbSession, response: Response) -> MessageResponse:
    """Needs the current password. All other sessions of the account end; this browser stays signed in."""
    key = f"change|{user.user_id}"
    login_limiter.check(key)
    try:
        version = auth_service.change_password(db, user, payload.current_password, payload.new_password)
    except BadRequestError:
        login_limiter.record_failure(key)
        raise
    login_limiter.reset(key)
    sessions.set_session_cookie(response, sessions.create_session_token(user.user_id, user.role, version))
    return MessageResponse(message="Password changed. Other devices have been signed out.")


@router.post("/password/forgot", response_model=MessageResponse, summary="Email a password reset code",
             responses=error_responses(422, 503))
def forgot_password(payload: EmailRequest, db: DbSession) -> MessageResponse:
    """Always answers the same, whether or not the address has an account. 503 if this server cannot send email."""
    auth_service.request_password_reset(db, payload.email)
    return MessageResponse(message=FORGOT_MESSAGE)


@router.post("/password/reset", response_model=MessageResponse, summary="Set a new password with an emailed code",
             responses=error_responses(400, 422, 503))
def reset_password(payload: PasswordResetRequest, db: DbSession, response: Response) -> MessageResponse:
    """Ends every session of the account. Does not sign in: use the new password at `/auth/login`."""
    auth_service.reset_password(db, payload.email, payload.code, payload.new_password)
    sessions.clear_session_cookie(response)
    return MessageResponse(message="Password updated. Please sign in with the new password.")


@router.post("/verify-2fa", response_model=MessageResponse, summary="Verify a sign-in code (optional module, not built)",
             responses=error_responses(422, 501))
def verify_2fa(payload: Verify2FARequest, db: DbSession, response: Response) -> MessageResponse:
    auth_service.verify_second_factor(db, payload)
    return MessageResponse(message="Authentication successful.")


@router.post("/resend-2fa", response_model=LoginResponse, summary="Resend a sign-in code (optional module, not built)",
             responses=error_responses(422, 501))
def resend_2fa(payload: Resend2FARequest, db: DbSession) -> LoginResponse:
    return auth_service.resend_second_factor(db, payload)
