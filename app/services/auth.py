"""Accounts: registration, email verification, sign-in, sessions, password change and reset.

* Passwords are stored only as Argon2id hashes; one-time codes only as keyed hashes (``app.services.otp``).
* Email verification applies when the server can send email (``settings.email_available``). Then a new
  account must confirm its address with an emailed code before it can sign in. With email off, accounts
  work without verification and password reset by email is unavailable.
* Answers never reveal whether an email address has an account: sign-in failures, code checks,
  "resend" and "forgot password" each have one outcome for every case an outsider could probe.
* Sessions are revocable: each account has a session version; sign-out and any password change raise
  it, which ends every session issued earlier.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InvalidRequestError,
    NotImplementedYet,
    PermissionDenied,
)
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    Resend2FARequest,
    Verify2FARequest,
)
from app.services import email as email_service
from app.services import otp, passwords
from app.settings import get_settings

logger = logging.getLogger("aquamap.auth")

BAD_CREDENTIALS = "Incorrect email or password."
BAD_CODE = "The code is incorrect or has expired. Request a new one."
EMAIL_NOT_VERIFIED = "Confirm your email address first: enter the code we emailed you, or request a new one."
SESSION_INVALID = "Session is not valid. Please sign in again."
_USER_COLUMNS = "user_id, email, full_name, role"


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _user(row: object) -> CurrentUser:
    m = row._mapping  # type: ignore[attr-defined]
    return CurrentUser(user_id=m["user_id"], email=m["email"], full_name=m["full_name"], role=m["role"])


def _find(db: Session, email: str) -> object | None:
    return db.execute(text(
        f"SELECT {_USER_COLUMNS}, password_hash, is_active, email_verified, session_version FROM users WHERE email = :e"),
        {"e": normalise_email(email)}).one_or_none()


def _send_code(db: Session, user: CurrentUser, purpose: otp.Purpose) -> None:
    """Store a fresh code and email it. The code exists in clear only inside this call and the email."""
    code = otp.issue(db, user.user_id, purpose)
    db.commit()  # the stored code must survive even if delivery fails, so "resend" behaves predictably
    email_service.send_otp_email(user.email, user.full_name, code, purpose, get_settings().otp_ttl_minutes)


# ---------------------------------------------------------------------------- registration and verification
def register_user(db: Session, payload: RegisterRequest, *, role: str = "user",
                  verified: bool = False) -> tuple[CurrentUser, bool]:
    """Create an account. Returns ``(user, verification_required)``.

    From the API the role is always ``user``. ``verified=True`` is for accounts an operator creates on
    the command line. Raises ``ConflictError`` if the email is already registered.
    """
    email = normalise_email(payload.email)
    try:
        with db.begin_nested():  # a duplicate must not poison the caller's transaction
            row = db.execute(text(
                "INSERT INTO users (email, password_hash, full_name, role, email_verified, password_changed_at) "
                f"VALUES (:e, :h, :n, :r, :v, now()) RETURNING {_USER_COLUMNS}"),
                {"e": email, "h": passwords.hash_password(payload.password), "n": payload.full_name, "r": role,
                 "v": verified}).one()
    except IntegrityError as exc:
        raise ConflictError("An account with this email already exists.") from exc
    db.commit()
    user = _user(row)
    logger.info("account created: role=%s", role)
    needs_verification = get_settings().email_available and not verified
    if needs_verification:
        _send_code(db, user, "verify_email")
    return user, needs_verification


def verify_email(db: Session, email: str, code: str) -> None:
    """Confirm an address with its emailed code. One generic error for every way this can fail."""
    row = _find(db, email)
    if row is None or not row.is_active or row.email_verified:
        raise BadRequestError(BAD_CODE)
    ok = otp.verify(db, row.user_id, "verify_email", code)
    if ok:
        db.execute(text("UPDATE users SET email_verified = TRUE WHERE user_id = :u"), {"u": row.user_id})
    db.commit()  # also keeps the failed-attempt count
    if not ok:
        raise BadRequestError(BAD_CODE)
    logger.info("email verified")


def resend_verification(db: Session, email: str) -> None:
    """Email a new verification code if that makes sense; say nothing either way (the caller answers generically)."""
    if not get_settings().email_available:
        return
    row = _find(db, email)
    if row is None or not row.is_active or row.email_verified:
        return
    if otp.seconds_until_resend(db, row.user_id, "verify_email") > 0:
        return
    try:
        _send_code(db, _user(row), "verify_email")
    except email_service.EmailDeliveryError:
        logger.error("verification email could not be sent")


# ---------------------------------------------------------------------------- sign-in and sessions
def authenticate(db: Session, email: str, password: str) -> tuple[CurrentUser, int]:
    """``(user, session version)`` for correct credentials of an active, verified account."""
    row = _find(db, email)
    if row is None:
        passwords.burn_time(password)
        raise AuthenticationError(BAD_CREDENTIALS)
    if not passwords.verify_password(password, row.password_hash) or not row.is_active:
        raise AuthenticationError(BAD_CREDENTIALS)
    # Only someone who knows the password learns that the address is unconfirmed.
    if get_settings().email_available and not row.email_verified:
        raise PermissionDenied(EMAIL_NOT_VERIFIED, code="email_not_verified")
    if passwords.needs_rehash(row.password_hash):
        db.execute(text("UPDATE users SET password_hash = :h WHERE user_id = :u"),
                   {"h": passwords.hash_password(password), "u": row.user_id})
        db.commit()
    return _user(row), int(row.session_version)


def start_login(db: Session, payload: LoginRequest) -> tuple[LoginResponse, CurrentUser, int]:
    """Check credentials. Returns the response, the user and the session version to sign into the token."""
    settings = get_settings()
    if settings.enable_2fa:
        raise NotImplementedYet("email second factor at sign-in (set ENABLE_2FA=false)")
    user, version = authenticate(db, payload.email, payload.password)
    return LoginResponse(status="authenticated", requires_2fa=False, user=user,
                         expires_in=settings.session_ttl_hours * 3600), user, version


def get_active_user(db: Session, user_id: UUID, session_version: int | None = None) -> CurrentUser:
    """The account behind a session, read fresh. ``AuthenticationError`` if it is gone, disabled, or the
    session was ended (its version is older than the account's)."""
    row = db.execute(text(f"SELECT {_USER_COLUMNS}, session_version FROM users WHERE user_id = :u AND is_active"),
                     {"u": user_id}).one_or_none()
    if row is None or (session_version is not None and int(row.session_version) != session_version):
        raise AuthenticationError(SESSION_INVALID)
    return _user(row)


def end_sessions(db: Session, user_id: UUID) -> int:
    """End every session of the account (sign-out everywhere). Returns the new session version."""
    version = db.execute(text("UPDATE users SET session_version = session_version + 1 WHERE user_id = :u "
                              "RETURNING session_version"), {"u": user_id}).scalar()
    db.commit()
    return int(version) if version is not None else 0


# ---------------------------------------------------------------------------- passwords
def _set_password(db: Session, user_id: UUID, new_password: str, *, verify_email_too: bool = False) -> int:
    extra = ", email_verified = TRUE" if verify_email_too else ""
    version = db.execute(text(
        f"UPDATE users SET password_hash = :h, password_changed_at = now(), session_version = session_version + 1{extra} "
        "WHERE user_id = :u RETURNING session_version"),
        {"h": passwords.hash_password(new_password), "u": user_id}).scalar_one()
    db.execute(text("UPDATE auth_otps SET consumed = TRUE WHERE user_id = :u AND purpose = 'reset_password' AND NOT consumed"),
               {"u": user_id})
    return int(version)


def change_password(db: Session, user: CurrentUser, current_password: str, new_password: str) -> int:
    """Change the password of the signed-in user. Ends all sessions; returns the new session version so the
    caller can keep THIS browser signed in with a fresh cookie."""
    stored = db.execute(text("SELECT password_hash FROM users WHERE user_id = :u"), {"u": user.user_id}).scalar_one()
    if not passwords.verify_password(current_password, stored):
        raise BadRequestError("The current password is incorrect.", code="wrong_current_password")
    if passwords.verify_password(new_password, stored):
        raise InvalidRequestError("The new password must be different from the current one.")
    version = _set_password(db, user.user_id, new_password)
    db.commit()
    logger.info("password changed")
    return version


def request_password_reset(db: Session, email: str) -> None:
    """Email a reset code if the account exists; say nothing either way. 503 only if the server has no email at all."""
    if not get_settings().email_available:
        raise email_service.EmailDeliveryError("Password reset by email is not available on this server.")
    row = _find(db, email)
    if row is None or not row.is_active:
        passwords.burn_time(email)  # comparable work, so timing does not reveal which addresses have accounts
        return
    if otp.seconds_until_resend(db, row.user_id, "reset_password") > 0:
        return
    try:
        _send_code(db, _user(row), "reset_password")
    except email_service.EmailDeliveryError:
        logger.error("password reset email could not be sent")


def reset_password(db: Session, email: str, code: str, new_password: str) -> None:
    """Set a new password with an emailed code. Ends all sessions. Receiving the code also proves the address."""
    row = _find(db, email)
    if row is None or not row.is_active:
        raise BadRequestError(BAD_CODE)
    ok = otp.verify(db, row.user_id, "reset_password", code)
    if ok:
        _set_password(db, row.user_id, new_password, verify_email_too=True)
    db.commit()
    if not ok:
        raise BadRequestError(BAD_CODE)
    logger.info("password reset")


# ---------------------------------------------------------------------------- optional second factor (not built)
def verify_second_factor(db: Session, payload: Verify2FARequest) -> CurrentUser:
    raise NotImplementedYet("2FA verification")


def resend_second_factor(db: Session, payload: Resend2FARequest) -> LoginResponse:
    raise NotImplementedYet("2FA resend")
