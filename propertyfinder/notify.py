"""The mail send — separated from `digest.py` on purpose.

Building the digest is a pure function of the database; sending it is an I/O side effect
that opens a socket, and the two must never be the same function or the digest becomes
untestable without a mail server. `send_email` is the only thing here, and it is small
enough that the separation costs nothing: everything it needs travels in `Settings`, so a
caller with no SMTP configured gets a clear `False` back and can print the body instead —
which is exactly what `daily` does, so the pipeline never depends on a mailserver existing.

Unconfigured SMTP is not an error. A household running this the first day, before anyone
has bothered to fill in `smtp_host`, should get its digest on the terminal, not a traceback.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from propertyfinder.config import Settings

log = logging.getLogger(__name__)


def email_configured(settings: Settings) -> bool:
    """Whether there is enough in `Settings` to attempt a send at all."""
    return bool(settings.smtp_host and settings.alert_email_from and settings.alert_email_to)


def send_email(settings: Settings, subject: str, body: str) -> bool:
    """Send `body` as plain text. Returns True on send, False if SMTP is not configured.

    Recipients may be comma-separated in `alert_email_to`. A caller that gets `False` back
    has not failed — it has learned that printing is the honest fallback here, which is why
    this never raises for the unconfigured case; it only raises if SMTP *is* configured and
    the send itself goes wrong, which is a real failure worth seeing.
    """
    if not email_configured(settings):
        log.info("email not sent: SMTP not configured (set smtp_host / alert_email_* in .env)")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.alert_email_from
    recipients = [a.strip() for a in settings.alert_email_to.split(",") if a.strip()]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg, to_addrs=recipients)
    log.info("digest emailed to %s", recipients)
    return True
