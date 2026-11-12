"""Outgoing email for one-time codes. Only used when ``ENABLE_2FA`` is true (optional, later phase)."""

from __future__ import annotations

from app.errors import NotImplementedYet


def send_otp_email(to_address: str, full_name: str, code: str) -> None:
    raise NotImplementedYet("sending verification emails")
