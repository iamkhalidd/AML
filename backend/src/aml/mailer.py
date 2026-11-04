"""Outgoing email over SMTP. Without ``AML_SMTP_HOST`` the message is logged instead."""

import logging
import smtplib
from email.message import EmailMessage

from aml.config import get_settings

log = logging.getLogger(__name__)


def send(to: str, subject: str, body: str) -> bool:
    """Send one plain-text email; returns ``False`` when it was only logged."""
    settings = get_settings()
    if not settings.smtp_host:
        log.info("email to %s not sent (no SMTP host): %s", to, subject)
        return False
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)
    return True
