"""Registration, sign-in and loading the signed-in user.

Passwords are stored only as Argon2id hashes. Sign-in failures give one generic message whether the
email is unknown, the password is wrong or the account is disabled. The email second factor
(``ENABLE_2FA``) is an optional module that is not built; with it switched on, sign-in answers 501
instead of silently skipping the second step.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AuthenticationError, ConflictError, NotImplementedYet
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    Resend2FARequest,
    Verify2FARequest,
)
from app.services import passwords
from app.settings import get_settings

logger = logging.getLogger("aquamap.auth")

BAD_CREDENTIALS = "Incorrect email or password."
_USER_COLUMNS = "user_id, email, full_name, role"


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _user(row: object) -> CurrentUser:
    m = row._mapping  # type: ignore[attr-defined]
    return CurrentUser(user_id=m["user_id"], email=m["email"], full_name=m["full_name"], role=m["role"])


def register_user(db: Session, payload: RegisterRequest, *, role: str = "user") -> CurrentUser:
    """Create an account (always role ``user`` from the API); raise ``ConflictError`` if the email exists."""
    email = normalise_email(payload.email)
    try:
        with db.begin_nested():  # a duplicate must not poison the caller's transaction
            row = db.execute(text(
                "INSERT INTO users (email, password_hash, full_name, role) VALUES (:e, :h, :n, :r) "
                f"RETURNING {_USER_COLUMNS}"),
                {"e": email, "h": passwords.hash_password(payload.password), "n": payload.full_name, "r": role}).one()
    except IntegrityError as exc:
        raise ConflictError("An account with this email already exists.") from exc
    db.commit()
    logger.info("account created: role=%s", role)
    return _user(row)


def authenticate(db: Session, email: str, password: str) -> CurrentUser:
    """Return the user for correct credentials of an active account; otherwise ``AuthenticationError``."""
    row = db.execute(text(f"SELECT {_USER_COLUMNS}, password_hash, is_active FROM users WHERE email = :e"),
                     {"e": normalise_email(email)}).one_or_none()
    if row is None:
        passwords.burn_time(password)
        raise AuthenticationError(BAD_CREDENTIALS)
    if not passwords.verify_password(password, row.password_hash) or not row.is_active:
        raise AuthenticationError(BAD_CREDENTIALS)
    if passwords.needs_rehash(row.password_hash):
        db.execute(text("UPDATE users SET password_hash = :h WHERE user_id = :u"),
                   {"h": passwords.hash_password(password), "u": row.user_id})
        db.commit()
    return _user(row)


def start_login(db: Session, payload: LoginRequest) -> tuple[LoginResponse, CurrentUser | None]:
    """Check credentials. Returns the response plus the user when no second factor is required."""
    settings = get_settings()
    if settings.enable_2fa:
        raise NotImplementedYet("email second factor (set ENABLE_2FA=false)")
    user = authenticate(db, payload.email, payload.password)
    return LoginResponse(status="authenticated", requires_2fa=False, user=user,
                         expires_in=settings.session_ttl_hours * 3600), user


def get_active_user(db: Session, user_id: UUID) -> CurrentUser:
    """The account behind a session, read fresh; raise ``AuthenticationError`` if it is gone or disabled."""
    row = db.execute(text(f"SELECT {_USER_COLUMNS} FROM users WHERE user_id = :u AND is_active"),
                     {"u": user_id}).one_or_none()
    if row is None:
        raise AuthenticationError("Session is not valid. Please sign in again.")
    return _user(row)


def verify_second_factor(db: Session, payload: Verify2FARequest) -> CurrentUser:
    """Validate the one-time code (optional module, not built)."""
    raise NotImplementedYet("2FA verification")


def resend_second_factor(db: Session, payload: Resend2FARequest) -> LoginResponse:
    """Issue a fresh one-time code (optional module, not built)."""
    raise NotImplementedYet("2FA resend")
