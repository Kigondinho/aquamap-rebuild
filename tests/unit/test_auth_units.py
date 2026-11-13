"""Password hashing, session tokens, the sign-in limiter and request validation. No database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from app.errors import AuthenticationError, TooManyRequestsError
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services import passwords, sessions
from app.services.rate_limit import FailureLimiter
from app.settings import get_settings

SECRET_WORD = "Correct-horse-9"


# ---------------------------------------------------------------- passwords
def test_hash_is_argon2id_salted_and_never_the_password() -> None:
    first, second = passwords.hash_password(SECRET_WORD), passwords.hash_password(SECRET_WORD)
    assert first.startswith("$argon2id$") and SECRET_WORD not in first
    assert first != second  # random salt
    assert len(first) <= 255  # fits users.password_hash


def test_verify_accepts_only_the_right_password() -> None:
    stored = passwords.hash_password(SECRET_WORD)
    assert passwords.verify_password(SECRET_WORD, stored)
    assert not passwords.verify_password(SECRET_WORD + "x", stored)
    assert not passwords.verify_password("", stored)
    assert not passwords.verify_password(SECRET_WORD, "not-a-hash")       # damaged hash: False, not an exception
    assert not passwords.verify_password(SECRET_WORD, SECRET_WORD)        # a plain-text "hash" never matches


def test_rehash_check() -> None:
    assert not passwords.needs_rehash(passwords.hash_password(SECRET_WORD))
    assert passwords.needs_rehash("not-a-hash")
    passwords.burn_time(SECRET_WORD)  # must not raise


# ---------------------------------------------------------------- session tokens
def test_token_round_trip() -> None:
    user = uuid4()
    assert sessions.decode_session_token(sessions.create_session_token(user, "admin")) == (user, "admin")


def test_token_holds_no_personal_data() -> None:
    payload = jwt.decode(sessions.create_session_token(uuid4(), "user"), options={"verify_signature": False})
    assert set(payload) == {"sub", "role", "typ", "iat", "exp"}


def test_expired_token_is_rejected() -> None:
    old = datetime.now(UTC) - timedelta(hours=get_settings().session_ttl_hours, minutes=1)
    with pytest.raises(AuthenticationError, match="expired"):
        sessions.decode_session_token(sessions.create_session_token(uuid4(), "user", now=old))


def test_token_expires_after_the_configured_lifetime() -> None:
    payload = jwt.decode(sessions.create_session_token(uuid4(), "user"), options={"verify_signature": False})
    assert payload["exp"] - payload["iat"] == get_settings().session_ttl_hours * 3600


@pytest.mark.parametrize("make", [
    lambda t: t[:-4] + ("AAAA" if not t.endswith("AAAA") else "BBBB"),                       # altered signature
    lambda t: jwt.encode({"sub": str(uuid4()), "typ": "session", "iat": 1, "exp": 9999999999}, "x" * 40, "HS256"),  # other key
    lambda t: jwt.encode({"sub": str(uuid4()), "typ": "pending", "iat": datetime.now(UTC),
                          "exp": datetime.now(UTC) + timedelta(hours=1)},
                         get_settings().secret_key.get_secret_value(), "HS256"),               # wrong kind of token
    lambda t: jwt.encode({"sub": "not-a-uuid", "typ": "session", "iat": datetime.now(UTC),
                          "exp": datetime.now(UTC) + timedelta(hours=1)},
                         get_settings().secret_key.get_secret_value(), "HS256"),
    lambda t: jwt.encode({"sub": str(uuid4()), "typ": "session"}, get_settings().secret_key.get_secret_value(), "HS256"),  # no expiry
    lambda t: jwt.encode({"sub": str(uuid4()), "typ": "session", "iat": 1, "exp": 9999999999}, "", "none"),  # unsigned
    lambda t: "", lambda t: "garbage", lambda t: "a.b.c",
])
def test_forged_or_malformed_tokens_are_rejected(make) -> None:
    good = sessions.create_session_token(uuid4(), "user")
    with pytest.raises(AuthenticationError):
        sessions.decode_session_token(make(good))


# ---------------------------------------------------------------- sign-in limiter
def test_limiter_blocks_after_the_allowed_failures_and_recovers() -> None:
    limiter = FailureLimiter(max_failures=3, window_seconds=60)
    for second in range(3):
        limiter.check("k", now=second)
        limiter.record_failure("k", now=second)
    with pytest.raises(TooManyRequestsError) as caught:
        limiter.check("k", now=10)
    assert caught.value.status_code == 429 and 0 < caught.value.retry_after <= 61
    limiter.check("other", now=10)            # other keys are not affected
    limiter.check("k", now=61.5)              # the oldest failure has left the window
    limiter.reset("k")
    limiter.check("k", now=11)


def test_limiter_memory_is_bounded() -> None:
    limiter = FailureLimiter(max_failures=3, window_seconds=60, max_keys=5)
    for i in range(50):
        limiter.record_failure(f"k{i}", now=0)
    assert len(limiter._failures) <= 5


# ---------------------------------------------------------------- request validation
def register(**overrides: object) -> RegisterRequest:
    values = {"full_name": "Wanjiru Kamau", "email": "wanjiru@example.com", "password": SECRET_WORD,
              "confirm_password": SECRET_WORD} | overrides
    return RegisterRequest(**values)  # type: ignore[arg-type]


def test_valid_registration_is_accepted_and_name_is_tidied() -> None:
    assert register(full_name="  Wanjiru   Kamau ").full_name == "Wanjiru Kamau"


@pytest.mark.parametrize("overrides", [
    {"confirm_password": SECRET_WORD + "x"},     # passwords differ
    {"password": "short1", "confirm_password": "short1"},
    {"password": "onlyletters", "confirm_password": "onlyletters"},
    {"password": "1234567890", "confirm_password": "1234567890"},
    {"password": "a1" * 70, "confirm_password": "a1" * 70},   # longer than 128
    {"email": "not-an-email"},
    {"email": "a" * 250 + "@example.com"},
    {"full_name": "A"},
    {"full_name": "   "},
    {"full_name": "N" * 121},
    {"role": "admin"},                            # extra field: a client cannot choose its role
    {"is_active": True},
])
def test_invalid_registration_is_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        register(**overrides)


def test_login_request_rejects_extra_fields_and_empty_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com", password="")
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com", password="x", role="admin")  # type: ignore[call-arg]
