"""One-time codes: six random digits, stored only as a keyed hash, single use, short-lived.

* The stored value is ``HMAC-SHA256(SECRET_KEY, purpose | user id | code)``. A copy of the database
  does not reveal codes and cannot be searched for them without the server key; a plain hash of a
  six-digit number could be reversed by trying all million values.
* A code works for one purpose and one user, until it expires (``OTP_TTL_MINUTES``), is used, is
  replaced by a newer code, or has been guessed wrongly ``OTP_MAX_ATTEMPTS`` times.
* A new code can be requested at most every ``OTP_RESEND_SECONDS``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.settings import get_settings

Purpose = Literal["verify_email", "reset_password"]
CODE_DIGITS = 6


def generate_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(purpose: str, user_id: UUID, code: str) -> str:
    key = get_settings().secret_key.get_secret_value().encode()
    return hmac.new(key, f"otp|{purpose}|{user_id}|{code}".encode(), hashlib.sha256).hexdigest()


def seconds_until_resend(db: Session, user_id: UUID, purpose: Purpose) -> int:
    """0 if a new code may be issued now; otherwise the seconds left to wait."""
    wait = db.execute(text(
        "SELECT ceil(:gap - extract(epoch FROM (now() - max(created_at)))) FROM auth_otps "
        "WHERE user_id = :u AND purpose = :p"),
        {"u": user_id, "p": purpose, "gap": get_settings().otp_resend_seconds}).scalar()
    return max(0, int(wait)) if wait is not None else 0


def issue(db: Session, user_id: UUID, purpose: Purpose) -> str:
    """Invalidate earlier codes of this purpose, store a new one (hashed) and return it in clear ONCE."""
    code = generate_code()
    db.execute(text("UPDATE auth_otps SET consumed = TRUE WHERE user_id = :u AND purpose = :p AND NOT consumed"),
               {"u": user_id, "p": purpose})
    db.execute(text(
        "INSERT INTO auth_otps (user_id, purpose, otp_hash, expires_at) "
        "VALUES (:u, :p, :h, now() + make_interval(mins => :ttl))"),
        {"u": user_id, "p": purpose, "h": hash_code(purpose, user_id, code), "ttl": get_settings().otp_ttl_minutes})
    return code


def verify(db: Session, user_id: UUID, purpose: Purpose, code: str) -> bool:
    """True exactly once for the right, unexpired, unused code. A wrong guess counts against the code."""
    row = db.execute(text(
        "SELECT otp_id, otp_hash, attempts FROM auth_otps WHERE user_id = :u AND purpose = :p AND NOT consumed "
        "AND expires_at > now() ORDER BY created_at DESC, otp_id DESC LIMIT 1 FOR UPDATE"),
        {"u": user_id, "p": purpose}).one_or_none()
    if row is None:
        return False
    if hmac.compare_digest(row.otp_hash, hash_code(purpose, user_id, code)):
        db.execute(text("UPDATE auth_otps SET consumed = TRUE WHERE otp_id = :o"), {"o": row.otp_id})
        return True
    used_up = row.attempts + 1 >= get_settings().otp_max_attempts
    db.execute(text("UPDATE auth_otps SET attempts = attempts + 1, consumed = :c WHERE otp_id = :o"),
               {"o": row.otp_id, "c": used_up})
    return False


def purge_expired(db: Session) -> int:
    """Delete codes that expired more than a day ago (housekeeping; safe to run any time)."""
    return db.execute(text("DELETE FROM auth_otps WHERE expires_at < now() - interval '1 day'")).rowcount
