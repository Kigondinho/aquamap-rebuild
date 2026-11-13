"""Password hashing with Argon2id (argon2-cffi). Plain passwords are never stored or logged."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # library defaults: Argon2id with a random salt per hash

# A valid hash of a random value, used to spend the same time when the account does not exist.
_DUMMY_HASH = _hasher.hash("aquamap-dummy-value-for-timing")


def hash_password(plain: str) -> str:
    """Return a salted Argon2id hash suitable for the ``users.password_hash`` column."""
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    """True if ``plain`` matches the stored hash. Never raises for a wrong password or a damaged hash."""
    try:
        return _hasher.verify(password_hash, plain)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash uses outdated parameters and should be upgraded at next login."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def burn_time(plain: str) -> None:
    """Do the work of a verification without a real account, so response time does not reveal which emails exist."""
    verify_password(plain, _DUMMY_HASH)
