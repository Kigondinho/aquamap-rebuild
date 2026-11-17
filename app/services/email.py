"""Outgoing email (one-time codes for email verification and password reset).

Backends, chosen by ``EMAIL_BACKEND`` (see ``app.settings``):

* ``smtp``     real delivery through the configured server, with STARTTLS by default.
* ``console``  development only: the message, including its code, is written to the server log and
               kept in a small in-memory outbox so a developer (or a test) can read it. Settings
               refuse this backend when ``APP_ENV=production``, so a code is never logged there.
* ``none``     email is off; callers check ``settings.email_available`` and skip the flows that need it.

A code is only ever placed in the email itself. It is never returned by the API, in any mode.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage

from app.errors import AquaMapError
from app.settings import get_settings

logger = logging.getLogger("aquamap.email")


class EmailDeliveryError(AquaMapError):
    status_code = 503
    default_detail = "The email could not be sent. Try again in a few minutes."


@dataclass(frozen=True)
class SentEmail:
    to: str
    subject: str
    body: str


# Development outbox (console backend only). Small and in memory; never filled in production.
outbox: deque[SentEmail] = deque(maxlen=50)


def send_email(to_address: str, subject: str, body: str) -> None:
    """Send one plain-text email with the configured backend. Raises ``EmailDeliveryError`` on failure."""
    settings = get_settings()
    backend = settings.resolved_email_backend
    if backend == "none":
        raise EmailDeliveryError("Email is not configured on this server.")
    if backend == "console":
        if settings.is_production:  # settings already forbid this; refuse again rather than log a secret
            raise EmailDeliveryError("Email is not configured on this server.")
        outbox.append(SentEmail(to_address, subject, body))
        logger.info("DEVELOPMENT EMAIL (not sent) to %s | %s\n%s", to_address, subject, body)
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.smtp_from, to_address, subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_user and settings.smtp_password is not None:
                server.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("email delivery failed (%s)", type(exc).__name__)  # no address, no body, no code
        raise EmailDeliveryError() from exc
    logger.info("email sent: %s", subject)


def send_otp_email(to_address: str, full_name: str, code: str, purpose: str, minutes: int) -> None:
    """The one-time code message for ``purpose`` (``verify_email`` or ``reset_password``)."""
    if purpose == "reset_password":
        subject = "AquaMap password reset code"
        intro = "Use this code to set a new AquaMap password:"
        ignore = "If you did not ask to reset your password, ignore this email; your password stays as it is."
    else:
        subject = "AquaMap email verification code"
        intro = "Use this code to confirm your email address for AquaMap:"
        ignore = "If you did not create an AquaMap account, ignore this email."
    body = (f"Hello {full_name},\n\n{intro}\n\n    {code}\n\n"
            f"The code expires in {minutes} minutes and can be used once. Never share it.\n\n{ignore}\n")
    send_email(to_address, subject, body)
