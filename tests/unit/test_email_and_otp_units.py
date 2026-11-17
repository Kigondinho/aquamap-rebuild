"""Email backends, one-time code hashing and cookie settings. No database, no network."""

from __future__ import annotations

import logging
import smtplib
from uuid import uuid4

import pytest
from fastapi import Response
from pydantic import ValidationError

from app.services import email as email_service
from app.services import otp, sessions
from app.settings import Settings, get_settings

KEY = "k" * 40


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **({"secret_key": KEY, "postgres_password": "pw", "email_backend": "auto"} | overrides))  # type: ignore[arg-type]


@pytest.fixture
def use_settings(monkeypatch: pytest.MonkeyPatch):
    """Run with specific settings by patching the environment and clearing the settings cache."""
    def apply(**env: str) -> Settings:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        return get_settings()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()
    email_service.outbox.clear()


# ---------------------------------------------------------------- which backend
@pytest.mark.parametrize(("overrides", "backend", "available"), [
    ({}, "console", True),                                                             # development, no SMTP
    ({"app_env": "test"}, "console", True),
    ({"app_env": "production"}, "none", False),                                        # production, no SMTP: email off
    ({"smtp_host": "mail.example.com", "smtp_from": "a@example.com"}, "smtp", True),
    ({"app_env": "production", "smtp_host": "mail.example.com", "smtp_from": "a@example.com"}, "smtp", True),
    ({"smtp_host": "mail.example.com"}, "console", True),                              # incomplete SMTP is not SMTP
    ({"smtp_host": "  ", "smtp_from": ""}, "console", True),                           # blank values from .env are unset
    ({"email_backend": "none"}, "none", False),
    ({"email_backend": "console"}, "console", True),
])
def test_backend_resolution(overrides: dict, backend: str, available: bool) -> None:
    s = settings(**overrides)
    assert (s.resolved_email_backend, s.email_available) == (backend, available)


def test_console_backend_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="console"):
        settings(app_env="production", email_backend="console")


def test_smtp_backend_needs_host_and_sender() -> None:
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        settings(email_backend="smtp")
    with pytest.raises(ValidationError):
        settings(email_backend="smtp", smtp_host="mail.example.com")


# ---------------------------------------------------------------- cookie settings
def test_cookie_is_secure_in_production_and_cannot_be_weakened_there() -> None:
    assert settings(app_env="production").cookie_secure is True
    assert settings().cookie_secure is False                      # plain http://localhost in development
    assert settings(session_cookie_secure=True).cookie_secure is True
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE"):
        settings(app_env="production", session_cookie_secure=False)
    with pytest.raises(ValidationError):
        settings(session_cookie_samesite="none")                  # only lax or strict


@pytest.mark.parametrize(("env", "secure"), [({"APP_ENV": "production"}, True), ({"APP_ENV": "development"}, False)])
def test_session_cookie_attributes(use_settings, env: dict, secure: bool) -> None:
    use_settings(**env)
    response = Response()
    sessions.set_session_cookie(response, sessions.create_session_token(uuid4(), "user"))
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "path=/" in header and "max-age=" in header
    assert ("secure" in header) is secure
    cleared = Response()
    sessions.clear_session_cookie(cleared)
    assert "max-age=0" in cleared.headers["set-cookie"].lower() and "httponly" in cleared.headers["set-cookie"].lower()


# ---------------------------------------------------------------- sending
def test_console_backend_logs_and_keeps_the_message_in_development(use_settings, caplog: pytest.LogCaptureFixture) -> None:
    use_settings(EMAIL_BACKEND="console", APP_ENV="development")
    with caplog.at_level(logging.INFO, logger="aquamap.email"):
        email_service.send_otp_email("dev@example.com", "Dev User", "424242", "verify_email", 10)
    sent = email_service.outbox[-1]
    assert sent.to == "dev@example.com" and "424242" in sent.body and "verification" in sent.subject
    assert "expires in 10 minutes" in sent.body and "424242" in caplog.text  # visible to the developer, by design


def test_reset_email_wording_differs(use_settings) -> None:
    use_settings(EMAIL_BACKEND="console", APP_ENV="development")
    email_service.send_otp_email("dev@example.com", "Dev User", "135790", "reset_password", 10)
    sent = email_service.outbox[-1]
    assert "password reset" in sent.subject and "135790" in sent.body and "did not ask" in sent.body


def test_nothing_is_logged_or_kept_when_email_is_off(use_settings, caplog: pytest.LogCaptureFixture) -> None:
    use_settings(EMAIL_BACKEND="none")
    with caplog.at_level(logging.DEBUG), pytest.raises(email_service.EmailDeliveryError):
        email_service.send_otp_email("x@example.com", "X", "777777", "verify_email", 10)
    assert "777777" not in caplog.text and not email_service.outbox


def test_production_never_writes_a_code_to_the_log(use_settings, caplog: pytest.LogCaptureFixture,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the console backend were somehow selected in production, the sender refuses."""
    s = use_settings(APP_ENV="production", EMAIL_BACKEND="none")
    monkeypatch.setattr(type(s), "resolved_email_backend", property(lambda self: "console"))
    with caplog.at_level(logging.DEBUG), pytest.raises(email_service.EmailDeliveryError):
        email_service.send_otp_email("x@example.com", "X", "888888", "verify_email", 10)
    assert "888888" not in caplog.text and not email_service.outbox


class FakeSMTP:
    instances: list[FakeSMTP] = []
    fail_on: str | None = None

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port, self.timeout, self.calls, self.message = host, port, timeout, [], None
        FakeSMTP.instances.append(self)
        if FakeSMTP.fail_on == "connect":
            raise OSError("connection refused")

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: object = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user, password))
        if FakeSMTP.fail_on == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, message: object) -> None:
        self.calls.append("send")
        self.message = message


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch):
    FakeSMTP.instances, FakeSMTP.fail_on = [], None
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_smtp_backend_uses_starttls_login_and_sends(use_settings, fake_smtp, caplog: pytest.LogCaptureFixture) -> None:
    smtp_secret = "smtp-" + uuid4().hex
    use_settings(APP_ENV="production", EMAIL_BACKEND="auto", SMTP_HOST="mail.example.com", SMTP_PORT="587", SMTP_USER="mailer",
                 SMTP_PASSWORD=smtp_secret, SMTP_FROM="AquaMap <no-reply@example.com>", SECRET_KEY="s" * 48,
                 POSTGRES_PASSWORD="not-a-placeholder")
    with caplog.at_level(logging.DEBUG):
        email_service.send_otp_email("user@example.com", "User", "246810", "verify_email", 10)
    server = fake_smtp.instances[0]
    assert (server.host, server.port, server.timeout) == ("mail.example.com", 587, 10)
    assert server.calls == ["starttls", ("login", "mailer", smtp_secret), "send", "quit"]  # encrypted before credentials
    assert server.message["To"] == "user@example.com" and server.message["From"] == "AquaMap <no-reply@example.com>"
    assert "246810" in server.message.get_content()
    assert "246810" not in caplog.text and smtp_secret not in caplog.text and "user@example.com" not in caplog.text
    assert not email_service.outbox  # nothing is kept in memory outside development


@pytest.mark.parametrize("stage", ["connect", "login"])
def test_smtp_failure_is_a_clean_error_without_secrets(use_settings, fake_smtp, caplog: pytest.LogCaptureFixture, stage: str) -> None:
    use_settings(EMAIL_BACKEND="smtp", SMTP_HOST="mail.example.com", SMTP_USER="mailer", SMTP_PASSWORD="smtp-pass-1",
                 SMTP_FROM="a@example.com")
    fake_smtp.fail_on = stage
    with caplog.at_level(logging.DEBUG), pytest.raises(email_service.EmailDeliveryError) as caught:
        email_service.send_otp_email("user@example.com", "User", "975310", "verify_email", 10)
    assert len(fake_smtp.instances) == 1  # the server really was contacted
    assert caught.value.detail == "The email could not be sent. Try again in a few minutes."
    assert caught.value.status_code == 503 and "975310" not in caught.value.detail
    assert "975310" not in caplog.text and "smtp-pass-1" not in caplog.text


# ---------------------------------------------------------------- one-time codes
def test_codes_are_six_digits_and_vary() -> None:
    codes = {otp.generate_code() for _ in range(300)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 290


def test_stored_value_is_a_keyed_hash_bound_to_user_and_purpose(use_settings) -> None:
    user, other = uuid4(), uuid4()
    stored = otp.hash_code("verify_email", user, "123456")
    assert len(stored) == 64 and "123456" not in stored and stored == otp.hash_code("verify_email", user, "123456")
    assert stored != otp.hash_code("reset_password", user, "123456")   # a code for one purpose is useless for the other
    assert stored != otp.hash_code("verify_email", other, "123456")    # and for another account
    assert stored != otp.hash_code("verify_email", user, "123457")
    use_settings(SECRET_KEY="another-secret-key-" + "z" * 30)
    assert stored != otp.hash_code("verify_email", user, "123456")     # useless without the server key
