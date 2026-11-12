"""Signed session and pending-2FA tokens (PyJWT). Implemented in a later phase."""

from __future__ import annotations

from uuid import UUID

from app.errors import NotImplementedYet


def create_session_token(user_id: UUID, role: str) -> str:
    """Token stored in the HTTP-only session cookie."""
    raise NotImplementedYet("session token creation")


def decode_session_token(token: str) -> tuple[UUID, str]:
    """Return ``(user_id, role)`` or raise ``AuthenticationError``."""
    raise NotImplementedYet("session token verification")


def create_pending_token(user_id: UUID, otp_id: int) -> str:
    """Short-lived token that links a login attempt to its one-time code."""
    raise NotImplementedYet("pending 2FA token creation")


def decode_pending_token(token: str) -> tuple[UUID, int]:
    """Return ``(user_id, otp_id)`` or raise ``AuthenticationError``."""
    raise NotImplementedYet("pending 2FA token verification")
