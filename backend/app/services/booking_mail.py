"""The e-mails customers get about a booking, written to be useful on their own:
what was booked, when, where (with a map link), a link to the booking, and a
calendar entry that adds the booking to their calendar, updates it when the
booking moves and removes it when the booking is cancelled.

Rendered when the e-mail is sent, from the booking as it is then.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote, urlparse

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.maps import google_maps_url
from app.core.timeutils import utcnow
from app.models import Booking, Notification, User
from app.models.enums import BookingStatus, NotificationType
from app.services import ics, settings_service
from app.services.availability import resource_timezone
from app.services.notifications import _clock

ACCENT = "#1f5e4c"

KINDS = {
    NotificationType.BOOKING_CONFIRMED: "confirmed",
    NotificationType.WAITLIST_PROMOTION: "promoted",
    NotificationType.BOOKING_RESCHEDULED: "moved",
    NotificationType.RESOURCE_CHANGED: "moved",
    NotificationType.BOOKING_CANCELLED: "cancelled",
    NotificationType.BOOKING_REMINDER: "reminder",
    NotificationType.BOOKING_CREATED: "received",
}


@dataclass
class BookingMail:
    subject: str
    text: str
    html: str
    # (calendar entry, its METHOD): None when there is nothing to add, change or remove.
    calendar: tuple[str, str] | None = None


@dataclass
class When:
    start: datetime
    end: datetime
    date: str  # "Fri 16 Oct 2026"
    time: str  # "2:00 PM – 5:00 PM"
    zone: str  # "Asia/Kolkata"

    @property
    def line(self) -> str:
        return f"{self.date}, {self.time} ({self.zone})"


def booking_url(booking_id) -> str:
    return f"{get_settings().frontend_url}/bookings/{booking_id}"


def calendar_uid(db: Session, booking: Booking) -> tuple[str, int]:
    """The calendar entry's id and revision. A rescheduled booking is a new booking
    row, so every booking in the chain shares the id of the first one; each move
    raises the revision, which is what makes calendars replace the entry."""
    root, depth = booking, 0
    while root.rescheduled_from_id and depth < 50:
        parent = db.get(Booking, root.rescheduled_from_id)
        if parent is None:
            break
        root, depth = parent, depth + 1
    host = urlparse(get_settings().frontend_url).hostname or "bookings"
    return f"{root.id}@{host}", depth


def _when(db: Session, booking: Booking) -> When:
    rules = settings_service.get_rules(db)
    zone = resource_timezone(booking.primary_resource, rules)
    start, end = booking.start_datetime.astimezone(zone), booking.end_datetime.astimezone(zone)
    if end.date() != start.date():  # past midnight: the end gets its own date
        time = f"{_clock(start)} – {end:%a %d %b}, {_clock(end)}"
    else:
        time = f"{_clock(start)} – {_clock(end)}"
    return When(start, end, f"{start:%a %d %b %Y}", time, zone.key)


def _google_calendar_url(booking: Booking, summary: str, place: str | None, details: str) -> str:
    stamp = "%Y%m%dT%H%M%SZ"
    dates = f"{booking.start_datetime.astimezone(UTC).strftime(stamp)}/{booking.end_datetime.astimezone(UTC).strftime(stamp)}"
    parts = {"action": "TEMPLATE", "text": summary, "dates": dates, "details": details, "location": place or ""}
    return "https://calendar.google.com/calendar/render?" + "&".join(f"{k}={quote(v, safe='/')}" for k, v in parts.items())


def render(db: Session, notification: Notification) -> BookingMail | None:
    """The rich version of a booking notification's e-mail, or None for the plain one."""
    kind = KINDS.get(notification.type)
    if kind is None or notification.booking_id is None:
        return None
    booking = db.get(Booking, notification.booking_id)
    user = db.get(User, notification.user_id)
    if booking is None or user is None:
        return None

    settings = get_settings()
    business = settings_service.get_rules(db).business_name
    service, resource, location = booking.service, booking.primary_resource, booking.primary_resource.location
    when = _when(db, booking)
    short = f"{when.start:%a %d %b}, {_clock(when.start)}"
    place = None
    if location is not None:
        place = location.name + (f", {location.address}" if location.address else "")
    map_url = google_maps_url(location.name, location.address, location.map_url) if location is not None else None
    maker = db.get(User, booking.created_by) if booking.created_by and booking.created_by != user.id else None
    link = booking_url(booking.id)
    summary = f"{service.name} with {resource.name}"

    was: When | None = None
    previous = db.get(Booking, booking.rescheduled_from_id) if kind == "moved" and booking.rescheduled_from_id else None
    if previous is not None:
        was = _when(db, previous)

    headings = {
        "confirmed": ("Your booking is confirmed", f"Booking confirmed: {service.name}, {short}"),
        "promoted": ("A place opened up: you're booked", f"You're booked: {service.name}, {short}"),
        "moved": ("Your booking has moved", f"Booking moved: {service.name}, now {short}"),
        "cancelled": ("Your booking was cancelled", f"Booking cancelled: {service.name}, {short}"),
        "reminder": (notification.title, notification.title),
        "received": (
            {BookingStatus.PENDING: "We received your booking", BookingStatus.WAITLISTED: "You are on the waitlist"}.get(
                booking.status, "Your booking was created"
            ),
            f"Booking received: {service.name}, {short}",
        ),
    }
    heading, subject = headings[kind]
    if kind == "received" and booking.status == BookingStatus.PENDING:
        intro = "It is not confirmed yet. We will e-mail you as soon as it is."
    elif kind == "received" and booking.status == BookingStatus.WAITLISTED:
        intro = "We will book you in and e-mail you if a place opens up."
    elif kind == "moved":
        intro = "Your calendar entry updates itself; the old time is replaced." if booking.status == BookingStatus.CONFIRMED else ""
    elif kind == "cancelled":
        intro = "It has been removed from your calendar."
        if booking.cancellation_reason:
            intro += f" Reason: {booking.cancellation_reason}"
    else:
        intro = ""

    calendar = None
    calendar_note = ""
    if kind in ("confirmed", "promoted") or (kind == "moved" and booking.status == BookingStatus.CONFIRMED):
        uid, revision = calendar_uid(db, booking)
        details = f"{summary}\nBooked at {business}.\nView or manage the booking: {link}"
        calendar = (
            ics.build(
                method="REQUEST", uid=uid, sequence=revision, start=booking.start_datetime, end=booking.end_datetime,
                stamp=utcnow(), summary=summary, description=details, location=place, url=link,
                organizer=(business, settings.smtp_from), attendee=(user.name, user.email),
            ),
            "REQUEST",
        )
        calendar_note = "A calendar invitation is attached" + (" and replaces the old entry." if revision else ".")
    elif kind == "cancelled":
        uid, revision = calendar_uid(db, booking)
        calendar = (
            ics.build(
                method="CANCEL", uid=uid, sequence=revision + 1, start=booking.start_datetime, end=booking.end_datetime,
                stamp=utcnow(), summary=summary, description=f"Cancelled. {summary}", location=place, url=link,
                organizer=(business, settings.smtp_from), attendee=(user.name, user.email),
            ),
            "CANCEL",
        )
    add_url = (
        _google_calendar_url(booking, summary, place, f"{summary}\nView or manage the booking: {link}")
        if kind in ("confirmed", "promoted", "moved") and booking.status == BookingStatus.CONFIRMED
        else None
    )

    # ---- plain text
    lines = [heading, ""]
    if intro:
        lines += [intro, ""]
    lines += [summary + (f" ({booking.quantity} places)" if booking.quantity > 1 else "")]
    if was is not None:
        lines += [f"Now:    {when.line}", f"Was:    {was.line}"]
    else:
        lines += [f"When:   {when.line}"]
    if place:
        lines += [f"Where:  {place}"]
    if map_url:
        lines += [f"Map:    {map_url}"]
    if maker is not None:
        lines += ["", f"Booked for you by {maker.name}."]
    lines += ["", f"View your booking: {link}"]
    if add_url:
        lines += [f"Add to Google Calendar: {add_url}"]
    if calendar_note:
        lines += ["", calendar_note]
    text = "\n".join(lines)

    # ---- HTML
    def button(href: str, label: str, primary: bool) -> str:
        style = (
            "display:inline-block;padding:10px 16px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;"
            + (f"background:{ACCENT};color:#ffffff;" if primary else f"background:#ffffff;color:{ACCENT};border:1px solid #ccc8be;")
        )
        return f'<a href="{escape(href, quote=True)}" style="{style}">{escape(label)}</a>'

    struck = "text-decoration:line-through;color:#8e8b82;" if kind == "cancelled" else ""
    muted = "color:#5f5b53;"
    rows = []
    if was is not None:
        rows.append(
            f'<div style="{muted}font-size:13px;margin-top:2px">Was <span style="text-decoration:line-through">{escape(was.date)}, {escape(was.time)}</span></div>'
        )
    where_html = ""
    if place:
        where_html = (
            f'<div style="margin-top:16px;font-size:13px;{muted}">Where</div>'
            f'<div style="font-size:15px">{escape(place)}</div>'
            + (f'<div style="margin-top:2px"><a href="{escape(map_url, quote=True)}" style="color:{ACCENT}">Open in Google Maps</a></div>' if map_url else "")
        )
    by_html = f'<div style="margin-top:16px;font-size:13px;{muted}">Booked for you by {escape(maker.name)}</div>' if maker is not None else ""
    buttons = [button(link, "View booking", True)]
    if add_url:
        buttons.append(button(add_url, "Add to Google Calendar", False))
    html = f"""<!doctype html>
<html><body style="margin:0;background:#f5f4f0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d1c1a">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">
  <div style="font-size:13px;{muted}">{escape(business)}</div>
  <h1 style="margin:4px 0 8px;font-size:22px;line-height:1.25">{escape(heading)}</h1>
  {f'<p style="margin:0 0 16px;font-size:15px;{muted}">{escape(intro)}</p>' if intro else ""}
  <div style="background:#ffffff;border:1px solid #e4e1d9;border-radius:12px;padding:20px">
    <div style="font-size:13px;{muted}">{escape(when.date)} ({escape(when.zone)})</div>
    <div style="font-size:22px;font-weight:600;margin:2px 0 0;{struck}">{escape(when.time)}</div>
    {''.join(rows)}
    <div style="margin-top:16px;font-size:16px;font-weight:600">{escape(service.name)}{f' <span style="font-weight:400;{muted}">({booking.quantity} places)</span>' if booking.quantity > 1 else ''}</div>
    <div style="{muted}">with {escape(resource.name)}</div>
    {where_html}
    {by_html}
  </div>
  <p style="margin:20px 0 8px">{"&nbsp;&nbsp;".join(buttons)}</p>
  {f'<p style="margin:12px 0 0;font-size:13px;{muted}">{escape(calendar_note)}</p>' if calendar_note else ""}
  <p style="margin:24px 0 0;font-size:12px;color:#8e8b82">You are receiving this because a booking was made for {escape(user.email)} at {escape(business)}.</p>
</div></body></html>"""
    return BookingMail(subject=subject, text=text, html=html, calendar=calendar)
