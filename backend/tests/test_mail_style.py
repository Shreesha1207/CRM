"""Every e-mail is styled like the booking e-mails: help requests, refunds, security notices,
password links and anything else. Only booking e-mails carry a calendar entry."""

import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Booking, Notification
from app.models.enums import BookingStatus, NotificationChannel, NotificationType
from app.services import notifications
from app.services.notifications import notify, send_email
from tests import test_booking_mail
from tests.agent_helpers import connect
from tests.test_booking_mail import mails_for, parts, visible

mailbox = test_booking_mail.mailbox  # the fixture that stands in for the mail server


def styled(message) -> tuple[str, str]:
    body = parts(message)
    assert "text/html" in body, f"{message['Subject']!r} was sent as plain text"
    assert "text/calendar" not in body and "application/ics" not in body
    return body["text/html"].get_content(), body["text/plain"].get_content()


def dispatch() -> None:
    with SessionLocal() as db:
        notifications.dispatch_pending(db)


def test_a_text_only_e_mail_is_still_sent_styled(mailbox) -> None:
    send_email("guest@example.com", "Hello there", "First line\nSecond line\n\nSee https://example.com/x?a=1&b=2")
    [message] = mailbox.sent
    html, text = styled(message)
    assert "Hello there" in html and "First line<br>Second line" in html
    assert '<a href="https://example.com/x?a=1&amp;b=2"' in html
    assert text.strip().startswith("First line")  # the text part stays as the fallback


def test_a_security_notice_has_the_booking_e_mails_look_and_a_button(client, db, user, user_headers, mailbox) -> None:
    client.post("/api/chat/subscription", headers=user_headers)
    connect(client, user_headers)
    dispatch()
    [message] = mails_for(mailbox, user.email)
    html, text = styled(message)
    assert message["Subject"] == "Claude was connected to your account"
    assert "background:#f5f4f0" in html and "border-radius:12px" in html  # the same page and card as booking e-mails
    assert re.search(r'<a href="[^"]+/connections"[^>]*>Review connected apps</a>', html)
    assert "Not you? Disconnect it" in visible(html)
    assert "Review connected apps: http" in text


def test_notices_about_a_booking_show_its_card(client, db, make, user, mailbox) -> None:
    service = make.service("Yoga Class", price=Decimal("12"))
    resource = make.resource("Studio", services=[service])
    booking = Booking(
        user_id=user.id, service_id=service.id, primary_resource_id=resource.id, start_datetime=datetime(2027, 3, 1, 10, tzinfo=UTC),
        end_datetime=datetime(2027, 3, 1, 11, tzinfo=UTC), quantity=1, status=BookingStatus.CONFIRMED,
    )  # fmt: skip
    db.add(booking)
    db.flush()
    notify(db, user_id=user.id, booking_id=booking.id, type=NotificationType.INVITATION_ACCEPTED, title="Invitation accepted", body="They said yes.")
    db.commit()
    dispatch()
    [message] = mails_for(mailbox, user.email)
    html, _ = styled(message)
    assert "Yoga Class" in html and "with Studio" in html and ">View booking</a>" in html


def test_every_notice_type_is_styled(db, user, mailbox) -> None:
    for kind in (NotificationType.PAYMENT_REFUND_DUE, NotificationType.CHAT_HELP_RESOLVED, NotificationType.PASSWORD_RESET):
        notify(db, user_id=user.id, type=kind, title=f"Notice {kind.value}", body="Something happened.", link="/bookings")
    db.commit()
    dispatch()
    sent = mails_for(mailbox, user.email)
    assert len(sent) == 3
    for message in sent:
        styled(message)
    assert db.scalar(select(Notification).where(Notification.channel == NotificationChannel.EMAIL, Notification.status != "SENT")) is None


def test_the_password_reset_e_mail_has_a_button(client, user, mailbox) -> None:
    client.post("/api/auth/forgot-password", json={"email": user.email})
    [message] = mails_for(mailbox, user.email)
    html, text = styled(message)
    assert re.search(r'<a href="[^"]+/reset-password\?token=[\w-]+"[^>]*>Choose a new password</a>', html)
    assert re.search(r"reset-password\?token=[\w-]+", text)
