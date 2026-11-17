# Phase 10 — Authentication and authorisation

Builds on the sign-in of Phase 9. A database created earlier is upgraded by `python -m app.db_tools init` (adds three
columns to `users`, one to `auth_otps` and one index; keeps all data).

## Endpoints

All under `/api/v1/auth`. Machine-readable description: [`openapi.json`](openapi.json).

| Need | Method and path | Signed in? | Result |
|---|---|---|---|
| Registration | POST `/register` | no | 201; `verification_required` says whether a code was emailed |
| Email verification | POST `/verify-email` (`email`, `code`) | no | 200, or 400 for any wrong, used or expired code |
| New verification code | POST `/resend-verification` (`email`) | no | Always the same 200 |
| Login | POST `/login` | no | Sets the session cookie; 401 bad credentials; 403 `email_not_verified`; 429 after repeated failures |
| Logout | POST `/logout` | either | Ends the session on the server and clears the cookie |
| Current user | GET `/me` | yes | `user_id`, `email`, `full_name`, `role` |
| Password change | POST `/password/change` (`current_password`, `new_password`, `confirm_password`) | yes | Other sessions end; this browser stays signed in |
| Password reset, step 1 | POST `/password/forgot` (`email`) | no | Always the same 200; 503 if the server has no email |
| Password reset, step 2 | POST `/password/reset` (`email`, `code`, `new_password`, `confirm_password`) | no | 200; all sessions end; sign in with the new password |

## Passwords

Stored only as Argon2id hashes with a random salt (argon2-cffi defaults). Rules: 8–128 characters with at least one
letter and one digit; the confirmation must match; a new password must differ from the current one. Passwords are
never returned, logged or echoed in an error. Five wrong "current password" attempts block further password changes
for 15 minutes (429).

## One-time codes (OTP)

Used for email verification and for password reset.

| Property | Value |
|---|---|
| Form | Six random digits from the operating system's secure generator |
| Storage | `HMAC-SHA256(SECRET_KEY, purpose, user id, code)` in `auth_otps.otp_hash`; never in clear |
| Lifetime | `OTP_TTL_MINUTES` (default 10) |
| Uses | One. A newer code cancels older ones |
| Wrong guesses | After `OTP_MAX_ATTEMPTS` (default 5) the code is cancelled; a new one must be requested |
| Request rate | One code per `OTP_RESEND_SECONDS` (default 60) per account and purpose |
| Scope | A code works for one account and one purpose: a verification code cannot reset a password |
| Exposure | Only in the email. Never in an API response, in any mode |

Why a keyed hash and not a plain hash: there are only a million six-digit codes, so a plain hash could be reversed by
trying them all. With the key, a copy of the database alone reveals nothing.

## Email verification

Verification applies **when the server can send email**. Then a new account gets a code by email and cannot sign in
until the address is confirmed. The 403 `email_not_verified` answer is given only after the correct password, so an
outsider cannot learn the state of an account. When the server cannot send email, accounts work without verification
and password reset by email answers 503. An account created while email was off is asked to verify once email is
switched on. A successful password reset also confirms the address, since it proves access to the mailbox. Accounts
created by an operator with `python -m app.db_tools create-user` are confirmed from the start.

### Email backends (`EMAIL_BACKEND`)

| Value | Behaviour |
|---|---|
| `auto` (default) | SMTP when `SMTP_HOST` and `SMTP_FROM` are set. Otherwise `console` in development and test, and `none` in production |
| `smtp` | Real delivery, STARTTLS before credentials are sent (`SMTP_STARTTLS=true`), 10-second timeout |
| `console` | **Development only.** The email, including its code, is written to the server log (`DEVELOPMENT EMAIL (not sent) ...`) so you can complete the flow without a mail server |
| `none` | No email, no verification, no reset by email |

Production safeguards: the application refuses to start with `EMAIL_BACKEND=console` when `APP_ENV=production`; `auto`
never chooses `console` there; and the sender itself refuses to log a message in production. With SMTP, the log
records only that a message was sent or failed: no address, no body, no code, no SMTP password.

## Sessions

- The session is a signed token (HS256, key `SECRET_KEY`) in a cookie. The token is never in a response body.
- **Cookie settings:** `HttpOnly` always, so scripts cannot read it. `SameSite=Lax` by default (`strict` is available),
  so it is not sent with cross-site POSTs. `Secure` whenever `APP_ENV=production`, so it only travels over HTTPS;
  production refuses `SESSION_COOKIE_SECURE=false`. In development `Secure` is off so plain `http://localhost` works.
  Lifetime `SESSION_TTL_HOURS` (12 hours).
- **Every request re-reads the account**: role, active state and session version come from the database. Disabling an
  account or changing its role takes effect on the next request.
- **Sessions can be ended.** Each account has a session version and each token carries the version it was issued
  under. Logout, password change and password reset raise the version, which ends every earlier session, including a
  copy of the cookie held elsewhere. Logout therefore signs out all devices of the account.

## Authorisation

The approved requirements define two roles. `user` may use the map data: roads, statistics and metadata.
`admin` may also read the `/admin` routes. Nothing finer is implemented. Registration always creates a `user` and
rejects extra fields such as `role`; administrators are created only from the command line. Every road, statistics,
metadata and admin route answers 401 without a valid session, and admin routes answer 403 to non-administrators.

## No account enumeration

| Request | Same answer for |
|---|---|
| Login | Unknown email, wrong password, disabled account (and takes about the same time) |
| Verify email, reset password | Wrong code, used code, expired code, unknown email |
| Resend verification, forgot password | Address with an account, without one, already confirmed, disabled |

Registration does say when an email is already registered (409); hiding that would make registration confusing, and
the design accepts it.

## Secrets

All from environment variables or `.env` (git-ignored): `SECRET_KEY` (signs sessions and keys the code hashes),
`POSTGRES_PASSWORD`, `SMTP_PASSWORD`. The application refuses to start without the first two, and refuses placeholder
values in production. A test scans the repository for literal passwords.

## Verification

Tests: 822 in total with a database; 493 run without one.

- `tests/db/test_auth_flows.py` (48, over HTTP against PostGIS): registration with a code; sign-in refused until
  verified; right, wrong, used, expired and malformed codes; cancellation after five guesses; resend wait and
  replacement; identical answers for known and unknown addresses; email off; logout ending the session for a copied
  cookie; expired and outdated tokens; every protected route without a session and with a signed-out cookie; role
  check; password change and reset, including that old sessions and the old password stop working.
- `tests/unit/test_email_and_otp_units.py` (23): backend selection, production refusals, cookie attributes in
  development and production, SMTP order (STARTTLS, login, send) and failure handling with no secrets logged, code
  hashing.
- Plus the Phase 9 tests for registration, sign-in, lock-out, tokens and the OpenAPI document.

On the real server (development backend, code read from the server log): register 201 → sign-in 403 until verified →
wrong code 400 → right code 200 → reused code 400 → sign-in 200 with `HttpOnly; SameSite=lax` → password change 200,
copy of the old cookie 401 → reset 200, previous password 401, new password 200 → logout, then the same cookie 401.
Both stored codes were 64-character hashes. The test account was removed afterwards.

## Limitations

- Real SMTP delivery was tested with a stand-in mail server object, not a live server; send yourself a code after
  configuring SMTP.
- The sign-in limiter is in the process: it resets on restart and is not shared between worker processes.
- Logout ends all sessions of the account; there is no per-device sign-out.
- State-changing requests are JSON POSTs protected by `SameSite=Lax`; there is no separate CSRF token.
- No second factor at sign-in (the `/verify-2fa` routes answer 501; leave `ENABLE_2FA=false`).
- Changing the email address of an account is not available.
- Expired codes are not deleted automatically (`app.services.otp.purge_expired` exists but is not scheduled).
