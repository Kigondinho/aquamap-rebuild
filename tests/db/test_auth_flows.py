"""Account flows over HTTP against PostGIS: verification by emailed code, sessions, password change and reset.

Email uses the development (console) backend: messages land in an in-memory outbox, where the test reads
the code a real user would read in their inbox. Passwords are generated per test. Everything runs in a
transaction that is rolled back.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.services import email as email_service
from app.services import sessions
from app.services.rate_limit import login_limiter
from app.settings import get_settings

pytestmark = pytest.mark.db

API = "/api/v1/auth"
COOKIE = "aquamap_session"
EMAIL = "wanjiru@example.com"
BAD_CODE = {"detail": "The code is incorrect or has expired. Request a new one."}


def new_password() -> str:
    return "Pw1-" + secrets.token_urlsafe(12)


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    login_limiter.reset()
    email_service.outbox.clear()
    yield
    login_limiter.reset()
    email_service.outbox.clear()
    get_settings.cache_clear()


@pytest.fixture
def email_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The server can send email (development backend), so verification and reset by email are on."""
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def db(conn: Connection) -> Iterator[Session]:
    with Session(bind=conn, join_transaction_mode="create_savepoint") as session:
        yield session


def make_client(db: Session) -> TestClient:
    from app.db import get_db
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    with make_client(db) as test_client:
        yield test_client


def register(client: TestClient, email: str = EMAIL, password: str | None = None):
    password = password or new_password()
    response = client.post(f"{API}/register", json={"full_name": "Wanjiru Kamau", "email": email, "password": password,
                                                    "confirm_password": password})
    assert response.status_code == 201, response.text
    return password, response


def login(client: TestClient, email: str, password: str):
    return client.post(f"{API}/login", json={"email": email, "password": password})


def emailed_code(to: str = EMAIL, subject_part: str = "") -> str:
    """The six digits in the newest message to ``to``: what the user would read in their inbox."""
    for message in reversed(email_service.outbox):
        if message.to == to and subject_part in message.subject:
            return re.search(r"\b(\d{6})\b", message.body).group(1)  # type: ignore[union-attr]
    raise AssertionError(f"no email to {to}")


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def allow_resend(conn: Connection) -> None:
    """Move stored codes two minutes into the past, as if the resend wait had elapsed."""
    conn.execute(text("UPDATE auth_otps SET created_at = created_at - interval '2 minutes'"))


def verified_user(client: TestClient) -> str:
    password, _ = register(client)
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": emailed_code()}).status_code == 200
    return password


# ================================================================ registration with verification
def test_registration_emails_a_code_and_never_returns_it(client: TestClient, conn: Connection, email_on: None) -> None:
    password, response = register(client)
    body = response.json()
    assert body["verification_required"] is True and "emailed you a code" in body["message"]
    assert COOKIE not in response.cookies                       # not signed in yet
    assert len(email_service.outbox) == 1 and email_service.outbox[0].to == EMAIL
    code = emailed_code()
    assert code not in response.text and password not in response.text
    row = conn.execute(text("SELECT u.email_verified, o.otp_hash, o.purpose, o.attempts, o.consumed, "
                            "o.expires_at - o.created_at AS life FROM users u JOIN auth_otps o USING (user_id) "
                            "WHERE u.email = :e"), {"e": EMAIL}).one()
    assert row.email_verified is False and (row.purpose, row.attempts, row.consumed) == ("verify_email", 0, False)
    assert re.fullmatch(r"[0-9a-f]{64}", row.otp_hash) and code not in row.otp_hash   # stored as a keyed hash only
    assert row.life == timedelta(minutes=get_settings().otp_ttl_minutes)  # OTP_TTL_MINUTES
    everything = str(conn.execute(text("SELECT * FROM auth_otps")).all()) + str(conn.execute(text("SELECT * FROM users")).all())
    assert code not in everything and password not in everything                     # no plaintext secret anywhere


def test_unverified_account_cannot_sign_in(client: TestClient, email_on: None) -> None:
    password, _ = register(client)
    refused = login(client, EMAIL, password)
    assert refused.status_code == 403 and refused.json()["code"] == "email_not_verified"
    assert "set-cookie" not in refused.headers
    # without the password an outsider learns nothing about the account's state
    assert login(client, EMAIL, password + "x").json() == {"detail": "Incorrect email or password."}


def test_verification_then_sign_in(client: TestClient, conn: Connection, email_on: None) -> None:
    password, _ = register(client)
    done = client.post(f"{API}/verify-email", json={"email": EMAIL, "code": emailed_code()})
    assert done.status_code == 200 and done.json() == {"message": "Email confirmed. You can sign in now."}
    assert conn.execute(text("SELECT email_verified FROM users WHERE email = :e"), {"e": EMAIL}).scalar_one() is True
    assert login(client, EMAIL, password).status_code == 200
    assert client.get(f"{API}/me").json()["email"] == EMAIL


def test_a_code_works_only_once(client: TestClient, conn: Connection, email_on: None) -> None:
    register(client)
    code = emailed_code()
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": code}).status_code == 200
    again = client.post(f"{API}/verify-email", json={"email": EMAIL, "code": code})
    assert again.status_code == 400 and again.json() == BAD_CODE
    assert conn.execute(text("SELECT bool_and(consumed) FROM auth_otps")).scalar_one() is True


def test_wrong_code_is_refused_with_one_generic_answer(client: TestClient, conn: Connection, email_on: None) -> None:
    register(client)
    code = emailed_code()
    bad = client.post(f"{API}/verify-email", json={"email": EMAIL, "code": wrong(code)})
    unknown = client.post(f"{API}/verify-email", json={"email": "nobody@example.com", "code": code})
    assert bad.status_code == unknown.status_code == 400 and bad.json() == unknown.json() == BAD_CODE
    assert conn.execute(text("SELECT attempts FROM auth_otps")).scalar_one() == 1
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": code}).status_code == 200  # still usable


def test_expired_code_is_refused(client: TestClient, conn: Connection, email_on: None) -> None:
    password, _ = register(client)
    code = emailed_code()
    conn.execute(text("UPDATE auth_otps SET expires_at = now() - interval '1 second'"))
    expired = client.post(f"{API}/verify-email", json={"email": EMAIL, "code": code})
    assert expired.status_code == 400 and expired.json() == BAD_CODE
    assert login(client, EMAIL, password).status_code == 403


def test_code_is_cancelled_after_five_wrong_guesses(client: TestClient, conn: Connection, email_on: None) -> None:
    register(client)
    code = emailed_code()
    for _ in range(5):
        assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": wrong(code)}).status_code == 400
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": code}).json() == BAD_CODE  # right code, too late
    assert tuple(conn.execute(text("SELECT attempts, consumed FROM auth_otps")).one()) == (5, True)
    allow_resend(conn)
    assert client.post(f"{API}/resend-verification", json={"email": EMAIL}).status_code == 200
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": emailed_code()}).status_code == 200


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12 456", ""])
def test_malformed_codes_are_rejected_before_any_check(client: TestClient, conn: Connection, email_on: None, code: str) -> None:
    register(client)
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": code}).status_code == 422
    assert conn.execute(text("SELECT attempts FROM auth_otps")).scalar_one() == 0


# ================================================================ resend
def test_resend_replaces_the_code_and_waits_between_emails(client: TestClient, conn: Connection, email_on: None) -> None:
    register(client)
    first = emailed_code()
    too_soon = client.post(f"{API}/resend-verification", json={"email": EMAIL})
    assert too_soon.status_code == 200 and len(email_service.outbox) == 1        # within the minute: nothing sent
    allow_resend(conn)
    assert client.post(f"{API}/resend-verification", json={"email": EMAIL}).status_code == 200
    assert len(email_service.outbox) == 2
    second = emailed_code()
    if second != first:  # equal only by a one-in-a-million coincidence
        assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": first}).json() == BAD_CODE  # old code is dead
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": second}).status_code == 200


def test_resend_answers_the_same_for_every_address(client: TestClient, conn: Connection, email_on: None) -> None:
    verified_user(client)
    register(client, "pending@example.com")
    allow_resend(conn)
    email_service.outbox.clear()
    answers = [client.post(f"{API}/resend-verification", json={"email": e})
               for e in ("nobody@example.com", EMAIL, "pending@example.com")]
    assert len({(r.status_code, r.text) for r in answers}) == 1 and answers[0].status_code == 200
    assert [m.to for m in email_service.outbox] == ["pending@example.com"]       # only the unconfirmed account got mail


# ================================================================ email switched off
def test_without_email_accounts_need_no_verification(client: TestClient) -> None:
    password, response = register(client)
    assert response.json()["verification_required"] is False and response.json()["message"] == "Account created. Please sign in."
    assert not email_service.outbox
    assert login(client, EMAIL, password).status_code == 200
    forgot = client.post(f"{API}/password/forgot", json={"email": EMAIL})
    assert forgot.status_code == 503 and "not available" in forgot.json()["detail"]


def test_accounts_created_without_email_must_verify_once_email_is_on(client: TestClient, conn: Connection,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    password, _ = register(client)                       # email off
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    assert login(client, EMAIL, password).json()["code"] == "email_not_verified"
    client.post(f"{API}/resend-verification", json={"email": EMAIL})
    assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": emailed_code()}).status_code == 200
    assert login(client, EMAIL, password).status_code == 200


# ================================================================ sessions and sign-out
def test_sign_out_ends_the_session_on_the_server(client: TestClient, db: Session, email_on: None) -> None:
    password = verified_user(client)
    login(client, EMAIL, password)
    stolen = client.cookies[COOKIE]                      # a copy of the cookie, as an attacker might hold
    assert client.post(f"{API}/logout").json() == {"message": "Signed out."}
    assert client.get(f"{API}/me").status_code == 401
    with make_client(db) as other:
        reused = other.get(f"{API}/me", headers={"Cookie": f"{COOKIE}={stolen}"})
    assert reused.status_code == 401 and reused.json()["detail"].startswith("Session is not valid")
    assert login(client, EMAIL, password).status_code == 200   # signing in again works


def test_sign_out_without_or_with_a_forged_cookie_changes_nothing(client: TestClient, conn: Connection, email_on: None) -> None:
    password = verified_user(client)
    login(client, EMAIL, password)
    good = client.cookies[COOKIE]
    client.cookies.clear()
    assert client.post(f"{API}/logout").status_code == 200
    user_id = conn.execute(text("SELECT user_id FROM users WHERE email = :e"), {"e": EMAIL}).scalar_one()
    stale = sessions.create_session_token(user_id, "user", version=99)       # validly signed, but not a live session
    assert client.post(f"{API}/logout", headers={"Cookie": f"{COOKIE}=garbage"}).status_code == 200
    assert client.post(f"{API}/logout", headers={"Cookie": f"{COOKIE}={stale}"}).status_code == 200
    assert conn.execute(text("SELECT session_version FROM users WHERE email = :e"), {"e": EMAIL}).scalar_one() == 0
    assert client.get(f"{API}/me", headers={"Cookie": f"{COOKIE}={good}"}).status_code == 200   # the real session survived


def test_expired_and_outdated_tokens_are_refused(client: TestClient, conn: Connection, email_on: None) -> None:
    password = verified_user(client)
    login(client, EMAIL, password)
    user_id = conn.execute(text("SELECT user_id FROM users WHERE email = :e"), {"e": EMAIL}).scalar_one()
    client.cookies.clear()
    send = lambda token: client.get(f"{API}/me", headers={"Cookie": f"{COOKIE}={token}"})  # noqa: E731
    assert send(sessions.create_session_token(user_id, "user", 0)).status_code == 200
    expired = send(sessions.create_session_token(user_id, "user", 0, now=datetime.now(UTC) - timedelta(hours=13)))
    assert expired.status_code == 401 and "expired" in expired.json()["detail"]
    assert send(sessions.create_session_token(user_id, "user", 1)).status_code == 401      # a version never issued
    conn.execute(text("UPDATE users SET session_version = 5 WHERE user_id = :u"), {"u": user_id})
    assert send(sessions.create_session_token(user_id, "user", 0)).status_code == 401      # issued before the bump


PROTECTED = [("GET", f"{API}/me"), ("POST", f"{API}/password/change"), ("GET", "/api/v1/roads?bbox=36.7,-1.4,36.9,-1.2"),
             ("GET", "/api/v1/roads/search?q=road"), ("GET", "/api/v1/roads/nearby?lat=-1.29&lon=36.82"),
             ("GET", "/api/v1/roads/1"), ("GET", "/api/v1/roads/summary"), ("GET", "/api/v1/roads/export"),
             ("GET", "/api/v1/meta/config"), ("GET", "/api/v1/analysis/metadata"), ("GET", "/api/v1/admin/layers"),
             ("GET", "/api/v1/admin/config"), ("GET", "/api/v1/admin/pipeline/status"), ("PUT", "/api/v1/admin/config")]


@pytest.mark.parametrize(("method", "url"), PROTECTED)
def test_no_protected_endpoint_answers_without_a_valid_session(client: TestClient, conn: Connection, email_on: None,
                                                               method: str, url: str) -> None:
    body = {"json": {"current_password": "x", "new_password": "New-pass-123", "confirm_password": "New-pass-123"}} \
        if method != "GET" else {}
    assert client.request(method, url, **body).status_code == 401                         # no cookie
    password = verified_user(client)
    login(client, EMAIL, password)
    token = client.cookies[COOKIE]
    client.post(f"{API}/logout")
    client.cookies.clear()
    after_logout = client.request(method, url, headers={"Cookie": f"{COOKIE}={token}"}, **body)
    assert after_logout.status_code == 401 and set(after_logout.json()) == {"detail"}     # signed-out cookie


def test_unverified_account_has_no_session_and_no_access(client: TestClient, email_on: None) -> None:
    password, _ = register(client)
    login(client, EMAIL, password)
    assert COOKIE not in client.cookies and client.get("/api/v1/roads/summary").status_code == 401


def test_administrator_routes_need_the_administrator_role(client: TestClient, conn: Connection, email_on: None) -> None:
    password = verified_user(client)
    login(client, EMAIL, password)
    assert client.get("/api/v1/admin/layers").status_code == 403
    assert client.get("/api/v1/roads/summary").status_code == 200   # ordinary routes need no special role
    conn.execute(text("UPDATE users SET role = 'admin' WHERE email = :e"), {"e": EMAIL})
    assert client.get("/api/v1/admin/layers").status_code == 200


# ================================================================ password change
def test_password_change(client: TestClient, db: Session, conn: Connection, email_on: None) -> None:
    old = verified_user(client)
    login(client, EMAIL, old)
    before = client.cookies[COOKIE]
    new = new_password()
    changed = client.post(f"{API}/password/change", json={"current_password": old, "new_password": new, "confirm_password": new})
    assert changed.status_code == 200 and "Password changed" in changed.json()["message"]
    assert old not in changed.text and new not in changed.text
    assert client.get(f"{API}/me").status_code == 200                       # this browser got a fresh cookie
    assert client.cookies[COOKIE] != before
    with make_client(db) as other:                                           # every other session ended
        assert other.get(f"{API}/me", headers={"Cookie": f"{COOKIE}={before}"}).status_code == 401
        assert login(other, EMAIL, old).status_code == 401
        assert login(other, EMAIL, new).status_code == 200
    row = conn.execute(text("SELECT password_hash, session_version, password_changed_at FROM users WHERE email = :e"),
                       {"e": EMAIL}).one()
    assert row.password_hash.startswith("$argon2id$") and new not in row.password_hash and row.session_version == 1
    assert row.password_changed_at is not None


@pytest.mark.parametrize(("change", "status", "code"), [
    ({"current_password": "Wrong-pass-9"}, 400, "wrong_current_password"),
    ({"new_password": "short1", "confirm_password": "short1"}, 422, None),
    ({"new_password": "nodigitshere", "confirm_password": "nodigitshere"}, 422, None),
    ({"confirm_password": "Different-123"}, 422, None),
    ({"extra": "field"}, 422, None),
])
def test_password_change_validation(client: TestClient, email_on: None, change: dict, status: int, code: str | None) -> None:
    old = verified_user(client)
    login(client, EMAIL, old)
    payload = {"current_password": old, "new_password": "Brand-new-123", "confirm_password": "Brand-new-123"} | change
    response = client.post(f"{API}/password/change", json=payload)
    assert response.status_code == status and response.json().get("code") == code
    assert login(client, EMAIL, old).status_code == 200                      # the password did not change


def test_new_password_must_differ_and_guessing_the_current_one_is_limited(client: TestClient, email_on: None) -> None:
    old = verified_user(client)
    login(client, EMAIL, old)
    same = client.post(f"{API}/password/change", json={"current_password": old, "new_password": old, "confirm_password": old})
    assert same.status_code == 422 and "different" in same.json()["detail"]
    for _ in range(5):
        assert client.post(f"{API}/password/change", json={"current_password": "Wrong-pass-9", "new_password": "Brand-new-123",
                                                           "confirm_password": "Brand-new-123"}).status_code == 400
    blocked = client.post(f"{API}/password/change", json={"current_password": old, "new_password": "Brand-new-123",
                                                          "confirm_password": "Brand-new-123"})
    assert blocked.status_code == 429


# ================================================================ password reset
def test_password_reset(client: TestClient, db: Session, conn: Connection, email_on: None) -> None:
    old = verified_user(client)
    login(client, EMAIL, old)
    session_before = client.cookies[COOKIE]
    asked = client.post(f"{API}/password/forgot", json={"email": EMAIL})
    assert asked.status_code == 200
    code = emailed_code(subject_part="password reset")
    assert code not in asked.text
    stored = conn.execute(text("SELECT otp_hash FROM auth_otps WHERE purpose = 'reset_password'")).scalar_one()
    assert code not in stored and len(stored) == 64
    new = new_password()
    done = client.post(f"{API}/password/reset", json={"email": EMAIL, "code": code, "new_password": new, "confirm_password": new})
    assert done.status_code == 200 and done.json() == {"message": "Password updated. Please sign in with the new password."}
    with make_client(db) as other:
        assert other.get(f"{API}/me", headers={"Cookie": f"{COOKIE}={session_before}"}).status_code == 401  # old sessions ended
        assert login(other, EMAIL, old).status_code == 401
        assert login(other, EMAIL, new).status_code == 200
    again = client.post(f"{API}/password/reset", json={"email": EMAIL, "code": code, "new_password": "Another-pass-1",
                                                       "confirm_password": "Another-pass-1"})
    assert again.status_code == 400 and again.json() == BAD_CODE             # single use


def test_forgot_password_answers_the_same_for_every_address(client: TestClient, conn: Connection, email_on: None) -> None:
    verified_user(client)
    email_service.outbox.clear()
    known = client.post(f"{API}/password/forgot", json={"email": EMAIL})
    unknown = client.post(f"{API}/password/forgot", json={"email": "nobody@example.com"})
    assert (known.status_code, known.text) == (unknown.status_code, unknown.text) and known.status_code == 200
    assert [m.to for m in email_service.outbox] == [EMAIL]
    client.post(f"{API}/password/forgot", json={"email": EMAIL})             # within the minute: no second email
    assert len(email_service.outbox) == 1
    conn.execute(text("UPDATE users SET is_active = FALSE WHERE email = :e"), {"e": EMAIL})
    allow_resend(conn)
    client.post(f"{API}/password/forgot", json={"email": EMAIL})
    assert len(email_service.outbox) == 1                                    # a disabled account gets no code


def test_reset_refuses_wrong_expired_and_wrong_purpose_codes(client: TestClient, conn: Connection, email_on: None) -> None:
    old, _ = register(client)
    verification_code = emailed_code()
    client.post(f"{API}/password/forgot", json={"email": EMAIL})
    reset_code = emailed_code(subject_part="password reset")
    new = {"new_password": "Brand-new-123", "confirm_password": "Brand-new-123"}
    reset = lambda code, email=EMAIL: client.post(f"{API}/password/reset", json={"email": email, "code": code, **new})  # noqa: E731
    assert reset(wrong(reset_code)).json() == BAD_CODE
    assert reset(reset_code, "nobody@example.com").json() == BAD_CODE
    if verification_code != reset_code:
        assert reset(verification_code).json() == BAD_CODE                   # a verification code cannot reset a password
        assert client.post(f"{API}/verify-email", json={"email": EMAIL, "code": reset_code}).json() == BAD_CODE  # nor the reverse
    conn.execute(text("UPDATE auth_otps SET expires_at = now() - interval '1 second' WHERE purpose = 'reset_password'"))
    assert reset(reset_code).json() == BAD_CODE
    assert conn.execute(text("SELECT session_version FROM users WHERE email = :e"), {"e": EMAIL}).scalar_one() == 0
    allow_resend(conn)
    client.post(f"{API}/password/forgot", json={"email": EMAIL})
    fresh = emailed_code(subject_part="password reset")
    assert client.post(f"{API}/password/reset", json={"email": EMAIL, "code": fresh, "new_password": "weak",
                                                      "confirm_password": "weak"}).status_code == 422
    assert reset(fresh).status_code == 200                                   # the rejected request did not burn the code


def test_reset_also_confirms_the_address(client: TestClient, conn: Connection, email_on: None) -> None:
    """Receiving the reset code proves ownership of the mailbox, so an unconfirmed account becomes confirmed."""
    register(client)
    client.post(f"{API}/password/forgot", json={"email": EMAIL})
    new = new_password()
    assert client.post(f"{API}/password/reset", json={"email": EMAIL, "code": emailed_code(subject_part="password reset"),
                                                      "new_password": new, "confirm_password": new}).status_code == 200
    assert conn.execute(text("SELECT email_verified FROM users WHERE email = :e"), {"e": EMAIL}).scalar_one() is True
    assert login(client, EMAIL, new).status_code == 200


def test_reset_code_is_cancelled_after_five_wrong_guesses(client: TestClient, email_on: None) -> None:
    verified_user(client)
    client.post(f"{API}/password/forgot", json={"email": EMAIL})
    code = emailed_code(subject_part="password reset")
    new = {"new_password": "Brand-new-123", "confirm_password": "Brand-new-123"}
    for _ in range(5):
        assert client.post(f"{API}/password/reset", json={"email": EMAIL, "code": wrong(code), **new}).status_code == 400
    assert client.post(f"{API}/password/reset", json={"email": EMAIL, "code": code, **new}).json() == BAD_CODE


# ================================================================ operator-created accounts
def test_create_user_command_makes_a_verified_account(db_engine, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import getpass

    from app import db_tools

    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    monkeypatch.setattr(db_tools, "get_engine", lambda: db_engine)
    password = new_password()
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": password)
    address = f"operator-{secrets.token_hex(4)}@example.com"
    try:
        assert db_tools.main(["create-user", "--email", address, "--name", "Site Operator", "--admin"]) == 0
        with db_engine.connect() as check:
            row = check.execute(text("SELECT role, email_verified, password_hash FROM users WHERE email = :e"), {"e": address}).one()
        assert (row.role, row.email_verified) == ("admin", True) and row.password_hash.startswith("$argon2id$")
        assert password not in capsys.readouterr().out and not email_service.outbox   # no code needed, none sent
    finally:
        with db_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM users WHERE email = :e"), {"e": address})
