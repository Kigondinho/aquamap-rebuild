"""Registration, login and second-factor flow. Implemented in a later phase."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import NotImplementedYet
from app.schemas.auth import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    Resend2FARequest,
    Verify2FARequest,
)


def register_user(db: Session, payload: RegisterRequest) -> CurrentUser:
    """Create an inactive-until-valid account; raise ``ConflictError`` if the email exists."""
    raise NotImplementedYet("user registration")


def start_login(db: Session, payload: LoginRequest) -> tuple[LoginResponse, CurrentUser | None]:
    """Check credentials. Returns the response plus the user when no second factor is required."""
    raise NotImplementedYet("login")


def verify_second_factor(db: Session, payload: Verify2FARequest) -> CurrentUser:
    """Validate the one-time code (attempt and expiry limits apply)."""
    raise NotImplementedYet("2FA verification")


def resend_second_factor(db: Session, payload: Resend2FARequest) -> LoginResponse:
    """Issue a fresh one-time code and pending token."""
    raise NotImplementedYet("2FA resend")
