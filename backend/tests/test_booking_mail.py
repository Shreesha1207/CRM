import re
import smtplib
from datetime import UTC, datetime, timedelta
from datetime import time as dtime

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Booking
from app.services import ics, notifications
from tests.conftest import MONDAY, at

ENTRY = dict(
    method="REQUEST", uid="abc@example.com", sequence=0,
    start=datetime(2027, 3, 1, 10, 0, tzinfo=UTC), end=datetime(2027, 3, 1, 11, 0, tzinfo=UTC), stamp=datetime(2027, 2, 26, 8, 0, tzinfo=UTC),
    summary="Consultation with Resource A", description="Booked at Acme.", location="HQ, 1 Main St",
    url="http://localhost:8080/bookings/1", organizer=("Acme", "bookings@example.com"), attendee=("Test User", "user@test.io"),
)


def unfold(entry: str) -> list[str]:
    return entry.replace("\r\n ", "").split("\r\n")[:-1]


# ------------------------------------------------------------------ the calendar entry


def test_an_entry_is_standard_ical() -> None:
    entry = ics.build(**ENTRY)
    assert entry.endswith("\r\n") and "\n" not in entry.replace("\r\n", "")  # CRLF line ends only
    lines = unfold(entry)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    assert {"METHOD:REQUEST", "UID:abc@example.com", "SEQUENCE:0", "STATUS:CONFIRMED", "VERSION:2.0"} <= set(lines)
    assert "DTSTART:20270301T100000Z" in lines and "DTEND:20270301T110000Z" in lines and "DTSTAMP:20270226T080000Z" in lines
    assert "LOCATION:HQ\\, 1 Main St" in lines  # commas are escaped
    assert 'ORGANIZER;CN="Acme":mailto:bookings@example.com' in lines
    assert any(line.startswith('ATTENDEE;CN="Test User"') and line.endswith("mailto:user@test.io") for line in lines)


def test_times_are_written_in_utc_whatever_zone_they_come_in() -> None:
    from zoneinfo import ZoneInfo

    local = datetime(2027, 3, 1, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    lines = unfold(ics.build(**{**ENTRY, "start": local, "end": local + timedelta(hours=1)}))
    assert "DTSTART:20270301T100000Z" in lines and "DTEND:20270301T110000Z" in lines


def test_long_lines_fold_without_breaking_characters_and_unfold_back() -> None:
    summary = "Café ☕ " + "très long titre, avec; des caractères spéciaux " * 6
    entry = ics.build(**{**ENTRY, "summary": summary})
    for physical in entry.split("\r\n")[:-1]:
        assert len(physical.encode("utf-8")) <= 75
    assert any(line.startswith(" ") for line in entry.split("\r\n"))  # it did fold
    text = [line for line in unfold(entry) if line.startswith("SUMMARY:")][0][len("SUMMARY:"):]
    assert text.replace("\\,", ",").replace("\\;", ";") == summary  # nothing lost or split


def test_special_characters_and_line_breaks_are_escaped() -> None:
    entry = ics.build(**{**ENTRY, "description": "Line one\nLine two; with, commas \\ and a backslash"})
    assert "DESCRIPTION:Line one\\nLine two\\; with\\, commas \\\\ and a backslash" in unfold(entry)


def test_a_cancellation_is_marked_cancelled() -> None:
    lines = unfold(ics.build(**{**ENTRY, "method": "CANCEL", "sequence": 3}))
    assert "METHOD:CANCEL" in lines and "STATUS:CANCELLED" in lines and "SEQUENCE:3" in lines


# ------------------------------------------------------------------ what customers receive


class Mailbox:
    """Stands in for the mail server and keeps every message."""

    sent: list = []

    def __init__(self, host, port, timeout=None, context=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, username, password):
        pass

    def send_message(self, message):
        Mailbox.sent.append(message)


@pytest.fixture
def mailbox(monkeypatch):
    Mailbox.sent = []
    monkeypatch.setattr(smtplib, "SMTP", Mailbox)
    for key, value in dict(EMAIL_BACKEND="smtp", SMTP_HOST="localhost", SMTP_PORT=25, SMTP_USERNAME="", SMTP_FROM="bookings@example.com").items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()
    yield Mailbox
    get_settings.cache_clear()


def mails_for(mailbox, address) -> list:
    return [m for m in mailbox.sent if m["To"] == address]


def parts(message) -> dict[str, object]:
    return {p.get_content_type(): p for p in message.walk() if not p.is_multipart()}


def calendar_of(message) -> list[str]:
    return unfold(parts(message)["text/calendar"].get_content().replace("\r\n", "\r\n").replace("\n", "\r\n").rstrip("\r\n") + "\r\n")


def line(entry: list[str], prefix: str) -> str:
    return next(x for x in entry if x.startswith(prefix))


def book(client, headers, service, resource, start=None):
    r = client.post("/api/bookings", headers=headers, json={"service_id": str(service.id), "resource_id": str(resource.id), "start": start or at(MONDAY, 10)})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_confirmation_says_what_when_and_where_and_links_to_the_booking(client, make, user, user_headers, mailbox) -> None:
    service = make.service(name="Strength training")
    place = make.location(name="Downtown")
    place.address = "12 MG Road, Bengaluru"
    make.db.commit()
    resource = make.resource("John Carter", services=[service], location=place, hours=make.weekdays(dtime(9), dtime(17)))
    booking = book(client, user_headers, service, resource)

    [message] = mails_for(mailbox, user.email)
    assert message["Subject"].startswith("Booking confirmed: Strength training, Mon 01 Mar, 10:00 AM")
    assert "<bookings@example.com>" in message["From"] and "Booking Platform" in message["From"]  # a name, not just an address
    body = parts(message)
    assert set(body) == {"text/plain", "text/html", "text/calendar", "application/ics"}
    text = body["text/plain"].get_content()
    assert "Strength training with John Carter" in text and "Mon 01 Mar 2027, 10:00 AM – 11:00 AM (UTC)" in text
    assert "Downtown, 12 MG Road, Bengaluru" in text and "google.com/maps" in text
    assert f"/bookings/{booking['id']}" in text and "Add to Google Calendar" in text
    html = body["text/html"].get_content()
    assert "Your booking is confirmed" in html and "Open in Google Maps" in html and f"/bookings/{booking['id']}" in html
    assert "View booking" in html and "Add to Google Calendar" in html
    assert body["application/ics"].get_filename() == "booking.ics"


def test_the_calendar_entry_matches_the_booking(client, basic, user, user_headers, mailbox) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource)
    [message] = mails_for(mailbox, user.email)
    assert parts(message)["text/calendar"].get_param("method") == "REQUEST"
    entry = calendar_of(message)
    assert "METHOD:REQUEST" in entry and "STATUS:CONFIRMED" in entry and "SEQUENCE:0" in entry
    assert line(entry, "UID:").startswith(f"UID:{booking['id']}@")
    assert "DTSTART:20270301T100000Z" in entry and "DTEND:20270301T110000Z" in entry
    assert f"SUMMARY:{service.name} with {resource.name}" in entry
    assert f"/bookings/{booking['id']}" in line(entry, "URL:")
    assert line(entry, "ATTENDEE").endswith(f"mailto:{user.email}")


def test_moving_a_booking_updates_the_calendar_entry_instead_of_adding_another(client, basic, user, user_headers, mailbox) -> None:
    service, resource = basic
    first = book(client, user_headers, service, resource, at(MONDAY, 10))
    mailbox.sent.clear()

    moved = client.post(f"/api/bookings/{first['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 14)})
    assert moved.status_code == 200, moved.text
    second = moved.json()
    assert second["id"] != first["id"]  # a new booking row ...

    [message] = mails_for(mailbox, user.email)
    assert message["Subject"].startswith("Booking moved: ") and "now Mon 01 Mar, 2:00 PM" in message["Subject"]
    text = parts(message)["text/plain"].get_content()
    assert "Now:    Mon 01 Mar 2027, 2:00 PM – 3:00 PM (UTC)" in text and "Was:    Mon 01 Mar 2027, 10:00 AM – 11:00 AM (UTC)" in text
    assert f"/bookings/{second['id']}" in text  # the link is to the booking that is current
    entry = calendar_of(message)
    # ... but the same calendar entry, one revision later, so calendars replace the old time.
    assert line(entry, "UID:").startswith(f"UID:{first['id']}@") and "SEQUENCE:1" in entry and "METHOD:REQUEST" in entry
    assert "DTSTART:20270301T140000Z" in entry

    mailbox.sent.clear()
    third = client.post(f"/api/bookings/{second['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 16)}).json()
    [again] = mails_for(mailbox, user.email)
    entry = calendar_of(again)
    assert line(entry, "UID:").startswith(f"UID:{first['id']}@") and "SEQUENCE:2" in entry
    assert f"/bookings/{third['id']}" in parts(again)["text/plain"].get_content()


def test_cancelling_removes_the_booking_from_the_calendar(client, basic, user, user_headers, mailbox) -> None:
    service, resource = basic
    first = book(client, user_headers, service, resource)
    moved = client.post(f"/api/bookings/{first['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 14)}).json()
    mailbox.sent.clear()

    assert client.post(f"/api/bookings/{moved['id']}/cancel", headers=user_headers, json={"reason": "Plans changed"}).status_code == 200

    [message] = mails_for(mailbox, user.email)
    assert message["Subject"].startswith("Booking cancelled: ")
    assert parts(message)["text/calendar"].get_param("method") == "CANCEL"
    entry = calendar_of(message)
    assert "METHOD:CANCEL" in entry and "STATUS:CANCELLED" in entry
    assert line(entry, "UID:").startswith(f"UID:{first['id']}@")  # the entry added by the first e-mail
    assert "SEQUENCE:2" in entry  # later than the last update, so it wins
    text = parts(message)["text/plain"].get_content()
    assert "removed from your calendar" in text and "Plans changed" in text
    assert "Add to Google Calendar" not in text


def test_a_reminder_has_the_details_but_no_second_calendar_entry(client, db, basic, user, user_headers, mailbox) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource)
    mailbox.sent.clear()
    start = db.scalar(select(Booking.start_datetime))
    notifications.queue_reminders(db, start - timedelta(hours=23))
    notifications.dispatch_pending(db)

    [message] = mails_for(mailbox, user.email)
    assert message["Subject"] == "Reminder: your booking starts in 24 hours"
    assert set(parts(message)) == {"text/plain", "text/html"}
    text = parts(message)["text/plain"].get_content()
    assert "Mon 01 Mar 2027, 10:00 AM" in text and f"/bookings/{booking['id']}" in text


def test_a_booking_waiting_for_the_admin_gets_no_calendar_entry_until_confirmed(client, db, basic, user, admin_headers, user_headers, mailbox) -> None:
    service, resource = basic
    client.put("/api/admin/settings", headers=admin_headers, json={"values": {"require_admin_confirmation": True}})
    booking = book(client, user_headers, service, resource)
    [received] = mails_for(mailbox, user.email)
    assert received["Subject"].startswith("Booking received: ") and "text/calendar" not in parts(received)
    assert "not confirmed yet" in parts(received)["text/plain"].get_content()

    mailbox.sent.clear()
    assert client.post(f"/api/admin/bookings/{booking['id']}/confirm", headers=admin_headers).status_code == 200
    [confirmed] = mails_for(mailbox, user.email)
    assert confirmed["Subject"].startswith("Booking confirmed: ") and "METHOD:REQUEST" in calendar_of(confirmed)


def test_names_are_escaped_in_the_html(client, make, user, user_headers, mailbox) -> None:
    service = make.service(name="Yoga <script>alert(1)</script> & co")
    resource = make.resource("Studio", services=[service], hours=make.weekdays(dtime(9), dtime(17)))
    book(client, user_headers, service, resource)
    [message] = mails_for(mailbox, user.email)
    html = parts(message)["text/html"].get_content()
    assert "<script>" not in html and "Yoga &lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in html
    assert re.search(r"<html", html) and "</html>" in html


def test_a_booking_made_for_someone_says_who_made_it(client, admin, user, basic, admin_headers, mailbox) -> None:
    service, resource = basic
    r = client.post(
        "/api/admin/bookings", headers=admin_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10), "user_id": str(user.id)},
    )
    assert r.status_code == 201, r.text
    [message] = mails_for(mailbox, user.email)
    assert "Booked for you by Admin" in parts(message)["text/plain"].get_content()
    assert "Booked for you by Admin" in parts(message)["text/html"].get_content()
