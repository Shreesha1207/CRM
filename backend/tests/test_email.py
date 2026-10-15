import logging
import smtplib
import socket
import ssl

import pytest

from app import send_test_email
from app.core.config import get_settings
from app.services.notifications import send_email


class FakeSMTP:
    """Records what the app does to a mail server instead of talking to one."""

    log: list[tuple] = []
    contexts: list = []
    fail_login: Exception | None = None

    def __init__(self, host, port, timeout=None):
        FakeSMTP.log.append((type(self).__name__, host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.log.append(("starttls",))
        FakeSMTP.contexts.append(context)

    def login(self, username, password):
        if FakeSMTP.fail_login:
            raise FakeSMTP.fail_login
        FakeSMTP.log.append(("login", username, password))

    def send_message(self, message):
        FakeSMTP.log.append(("send", message["From"], message["To"], message["Subject"], message.get_content().strip()))


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, host, port, timeout=None, context=None):
        super().__init__(host, port, timeout)
        FakeSMTP.contexts.append(context)


@pytest.fixture
def mail(monkeypatch):
    """Configure the mail settings for one test and watch the fake server."""
    FakeSMTP.log, FakeSMTP.contexts, FakeSMTP.fail_login = [], [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)

    def configure(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    yield configure
    get_settings.cache_clear()


GMAIL = dict(
    EMAIL_BACKEND="smtp", SMTP_HOST="smtp.gmail.com", SMTP_PORT=587,
    SMTP_USERNAME="me@gmail.com", SMTP_PASSWORD="abcdefghijklmnop", SMTP_FROM="me@gmail.com",
)


def test_gmail_style_settings_upgrade_to_tls_log_in_and_send(mail) -> None:
    mail(**GMAIL)
    send_email("guest@example.com", "Booking confirmed", "See you Monday.")
    assert FakeSMTP.log == [
        ("FakeSMTP", "smtp.gmail.com", 587),
        ("starttls",),
        ("login", "me@gmail.com", "abcdefghijklmnop"),
        ("send", "me@gmail.com", "guest@example.com", "Booking confirmed", "See you Monday."),
    ]


def test_the_server_certificate_is_always_verified(mail) -> None:
    mail(**GMAIL)
    send_email("guest@example.com", "Hello", "Body")
    mail(**{**GMAIL, "SMTP_PORT": 465})
    send_email("guest@example.com", "Hello", "Body")
    assert len(FakeSMTP.contexts) == 2  # STARTTLS on 587, then the secure connection on 465
    assert all(c is not None and c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname for c in FakeSMTP.contexts)


def test_port_465_is_secure_from_the_start(mail) -> None:
    mail(**{**GMAIL, "SMTP_PORT": 465})
    send_email("guest@example.com", "Hello", "Body")
    assert FakeSMTP.log[0] == ("FakeSMTPSSL", "smtp.gmail.com", 465)
    assert ("starttls",) not in FakeSMTP.log  # a secure connection is not upgraded again
    assert FakeSMTP.log[1] == ("login", "me@gmail.com", "abcdefghijklmnop")


def test_a_server_without_a_login_gets_a_plain_message(mail) -> None:
    mail(EMAIL_BACKEND="smtp", SMTP_HOST="localhost", SMTP_PORT=25, SMTP_USERNAME="", SMTP_PASSWORD="")
    send_email("guest@example.com", "Hello", "Body")
    assert [entry[0] for entry in FakeSMTP.log] == ["FakeSMTP", "send"]


def test_the_console_backend_only_logs(mail, caplog) -> None:
    mail(EMAIL_BACKEND="console")
    with caplog.at_level(logging.INFO, logger="notifications"):
        send_email("guest@example.com", "Hello", "Body")
    assert FakeSMTP.log == [] and "EMAIL to=guest@example.com" in caplog.text


# ------------------------------------------------------------------ python -m app.send_test_email


def test_the_test_command_sends_and_never_prints_the_password(mail, capsys) -> None:
    mail(**GMAIL)
    assert send_test_email.main(["--to", "me@gmail.com"]) == 0
    out = capsys.readouterr().out
    assert "smtp.gmail.com:587" in out and "STARTTLS and login" in out and "me@gmail.com" in out and "Sent to me@gmail.com" in out
    assert "abcdefghijklmnop" not in out
    assert FakeSMTP.log[-1][0] == "send" and FakeSMTP.log[-1][2] == "me@gmail.com"


def test_the_test_command_says_when_nothing_would_be_sent(mail, capsys) -> None:
    mail(EMAIL_BACKEND="console")
    assert send_test_email.main(["--to", "me@gmail.com"]) == 1
    assert "EMAIL_BACKEND=smtp" in capsys.readouterr().out and FakeSMTP.log == []


def test_a_gmail_login_refusal_points_to_app_passwords(mail, capsys) -> None:
    mail(**GMAIL)
    FakeSMTP.fail_login = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    assert send_test_email.main(["--to", "me@gmail.com"]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "App Password" in out and "abcdefghijklmnop" not in out


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (socket.gaierror(-2, "Name or service not known"), "Could not find the server"),
        (TimeoutError("timed out"), "Could not connect to smtp.gmail.com:587"),
        (ConnectionRefusedError(), "Could not connect to smtp.gmail.com:587"),
        (smtplib.SMTPSenderRefused(553, b"not allowed", "x@y.z"), "SMTP_FROM must be the same address"),
    ],
)
def test_connection_problems_are_explained(mail, capsys, error, hint) -> None:
    mail(**GMAIL)
    FakeSMTP.fail_login = error
    assert send_test_email.main(["--to", "me@gmail.com"]) == 1
    assert hint in capsys.readouterr().out
