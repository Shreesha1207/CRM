"""Send a test e-mail with the app's own mail settings, to check they work:

    docker compose exec backend python -m app.send_test_email --to you@example.com

It reports what is configured (never the password) and, when the mail server
says no, what usually fixes it.
"""

import argparse
import smtplib
import socket
import ssl
import sys

from app.core.config import get_settings
from app.services.notifications import send_email


def explain(exc: Exception, host: str, port: int) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            f"The mail server refused the login ({exc.smtp_code}). SMTP_USERNAME must be the full address. "
            "For Gmail, SMTP_PASSWORD must be an App Password (Google Account > Security > 2-Step Verification > "
            "App passwords), pasted without spaces, not your normal password."
        )
    if isinstance(exc, (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused)):
        return "The mail server refused an address. With Gmail, SMTP_FROM must be the same address as SMTP_USERNAME."
    if isinstance(exc, socket.gaierror):
        return f"Could not find the server '{host}'. Check SMTP_HOST (Gmail: smtp.gmail.com)."
    if isinstance(exc, (TimeoutError, ConnectionRefusedError)):
        return f"Could not connect to {host}:{port}. Check SMTP_HOST and SMTP_PORT (Gmail: smtp.gmail.com and 587) and that your network allows outgoing mail."
    if isinstance(exc, (ssl.SSLError, smtplib.SMTPNotSupportedError)):
        return (
            "The secure connection could not be set up or verified. Use port 587 (starts plain, then upgrades) or 465 "
            "(secure from the start). If that is already so, a network or antivirus that inspects encrypted traffic can cause this."
        )
    return "The message could not be sent."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a test e-mail with the app's mail settings.")
    parser.add_argument("--to", required=True, help="where to send it")
    args = parser.parse_args(argv)

    settings = get_settings()
    print(f"EMAIL_BACKEND = {settings.email_backend}")
    if settings.email_backend != "smtp":
        print("Nothing will be sent: set EMAIL_BACKEND=smtp (and the SMTP_* values) in .env, then restart the backend.")
        return 1
    how = "secure from the start" if settings.smtp_port == 465 else ("STARTTLS and login" if settings.smtp_username else "plain, no login")
    print(f"Server        = {settings.smtp_host}:{settings.smtp_port} ({how})")
    print(f"Login         = {settings.smtp_username or '(none)'}" + (" with a password" if settings.smtp_password else ""))
    print(f"From          = {settings.smtp_from}")
    try:
        send_email(
            args.to,
            "Test e-mail from your booking platform",
            "If you can read this, the mail settings work: booking confirmations and invitations will be delivered.",
        )
    except Exception as exc:
        print(f"\nFAILED: {explain(exc, settings.smtp_host, settings.smtp_port)}\nDetails: {type(exc).__name__}: {exc}")
        return 1
    print(f"\nSent to {args.to}. If it is not in the inbox in a minute, check the spam folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
