"""Password hashing (Argon2id via argon2-cffi). Implemented in a later phase."""

from __future__ import annotations

from app.errors import NotImplementedYet


def hash_password(plain: str) -> str:
    """Return a salted Argon2id hash suitable for the ``users.password_hash`` column."""
    raise NotImplementedYet("password hashing")


def verify_password(plain: str, password_hash: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    raise NotImplementedYet("password verification")


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash uses outdated parameters and should be upgraded at next login."""
    raise NotImplementedYet("password rehash check")
