"""The e-mails customers get about a booking, written to be useful on their own:
what was booked, when, where (with a map button), a link to the booking, and a
calendar invitation that puts the booking in their calendar, moves it when the
booking moves and takes it out when the booking is cancelled.

Rendered when the e-mail is sent, from the booking as it is then.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.maps import google_maps_url
from app.core.timeutils import utcnow
from app.models import Booking, BookingInvitation, Notification, User
from app.models.enums import BookingStatus, NotificationType
from app.services import ics, settings_service
from app.services.availability import resource_timezone
from app.services.notifications import _clock

ACCENT = "#1f5e4c"
MUTED = "color:#5f5b53;"

KINDS = {
    NotificationType.BOOKING_CONFIRMED: "confirmed",
    NotificationType.WAITLIST_PROMOTION: "promoted",
    NotificationType.BOOKING_RESCHEDULED: "moved",
    NotificationType.RESOURCE_CHANGED: "moved",
    NotificationType.BOOKING_CANCELLED: "cancelled",
    NotificationType.BOOKING_REMINDER: "reminder",
    NotificationType.BOOKING_CREATED: "received",
}

# What the customer is told about the calendar invitation the e-mail carries.
ANSWER_YES = "This e-mail is a calendar invitation: answer Yes to it to put the booking in your calendar."
FOLLOWS = "If the booking moves or is cancelled, your calendar entry follows."
NOT_THE_BOOKING = "Answering the invitation doesn't change the booking: to change or cancel it, use View booking."


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

    @property
    def short(self) -> str:
        return f"{self.start:%a %d %b}, {_clock(self.start)}"


@dataclass
class Facts:
    """What every e-mail about a booking shows."""

    business: str
    service: str
    resource: str
    quantity: int
    when: When
    was: When | None  # the time before, when it has just moved
    place: str | None
    map_url: str | None
    maker: str | None  # who booked it, when it was booked for the customer


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


def _facts(db: Session, booking: Booking, customer_id, previous: Booking | None = None) -> Facts:
    location = booking.primary_resource.location
    place = map_url = None
    if location is not None:
        place = location.name + (f", {location.address}" if location.address else "")
        map_url = google_maps_url(location.name, location.address, location.map_url)
    maker = db.get(User, booking.created_by) if booking.created_by and booking.created_by != customer_id else None
    return Facts(
        business=settings_service.get_rules(db).business_name,
        service=booking.service.name,
        resource=booking.primary_resource.name,
        quantity=booking.quantity,
        when=_when(db, booking),
        was=_when(db, previous) if previous is not None else None,
        place=place,
        map_url=map_url,
        maker=maker.name if maker is not None else None,
    )


# ---------------------------------------------------------------- building blocks


def _text_details(facts: Facts) -> list[str]:
    lines = [f"{facts.service} with {facts.resource}" + (f" ({facts.quantity} places)" if facts.quantity > 1 else "")]
    if facts.was is not None:
        lines += [f"Now:    {facts.when.line}", f"Was:    {facts.was.line}"]
    else:
        lines += [f"When:   {facts.when.line}"]
    if facts.place:
        lines += [f"Where:  {facts.place}"]
    if facts.maker:
        lines += ["", f"Booked for you by {facts.maker}."]
    return lines


def _button(href: str, label: str, *, primary: bool = True, small: bool = False) -> str:
    size = "padding:7px 12px;font-size:13px;" if small else "padding:10px 16px;font-size:14px;"
    colours = f"background:{ACCENT};color:#ffffff;border:1px solid {ACCENT};" if primary else f"background:#ffffff;color:{ACCENT};border:1px solid #ccc8be;"
    style = f"display:inline-block;{size}{colours}border-radius:8px;font-weight:600;text-decoration:none;margin:0 8px 8px 0"
    return f'<a href="{escape(href, quote=True)}" style="{style}">{escape(label)}</a>'


def _card(facts: Facts, *, struck: bool = False) -> str:
    strike = "text-decoration:line-through;color:#8e8b82;" if struck else ""
    was = ""
    if facts.was is not None:
        was = (
            f'<div style="{MUTED}font-size:13px;margin-top:2px">Was '
            f'<span style="text-decoration:line-through">{escape(facts.was.date)}, {escape(facts.was.time)}</span></div>'
        )
    places = f' <span style="font-weight:400;{MUTED}">({facts.quantity} places)</span>' if facts.quantity > 1 else ""
    where = ""
    if facts.place:
        where = (
            f'<div style="margin-top:16px;font-size:13px;{MUTED}">Where</div>'
            f'<div style="font-size:15px">{escape(facts.place)}</div>'
            + (f'<div style="margin-top:10px">{_button(facts.map_url, "Open in Google Maps", primary=False, small=True)}</div>' if facts.map_url else "")
        )
    by = f'<div style="margin-top:16px;font-size:13px;{MUTED}">Booked for you by {escape(facts.maker)}</div>' if facts.maker else ""
    return f"""<div style="background:#ffffff;border:1px solid #e4e1d9;border-radius:12px;padding:20px">
    <div style="font-size:13px;{MUTED}">{escape(facts.when.date)} ({escape(facts.when.zone)})</div>
    <div style="font-size:22px;font-weight:600;margin:2px 0 0;{strike}">{escape(facts.when.time)}</div>
    {was}
    <div style="margin-top:16px;font-size:16px;font-weight:600">{escape(facts.service)}{places}</div>
    <div style="{MUTED}">with {escape(facts.resource)}</div>
    {where}
    {by}
  </div>"""


def _page(*, business: str, heading: str, intro: str, card: str, buttons: list[str], notes: list[str], footer: str) -> str:
    """`notes` are trusted HTML; everything else that varies is escaped by the caller."""
    return f"""<!doctype html>
<html><body style="margin:0;background:#f5f4f0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d1c1a">
<div style="max-width:560px;margin:0 auto;padding:24px 16px">
  <div style="font-size:13px;{MUTED}">{escape(business)}</div>
  <h1 style="margin:4px 0 8px;font-size:22px;line-height:1.25">{escape(heading)}</h1>
  {f'<p style="margin:0 0 16px;font-size:15px;{MUTED}">{escape(intro)}</p>' if intro else ""}
  {card}
  <div style="margin:20px 0 4px">{"".join(buttons)}</div>
  {"".join(f'<p style="margin:8px 0 0;font-size:13px;{MUTED}">{note}</p>' for note in notes)}
  <p style="margin:24px 0 0;font-size:12px;color:#8e8b82">{escape(footer)}</p>
</div></body></html>"""


# ---------------------------------------------------------------- the e-mails


def render(db: Session, notification: Notification) -> BookingMail | None:
    """The rich version of a booking notification's e-mail, or None for the plain one."""
    kind = KINDS.get(notification.type)
    if kind is None or notification.booking_id is None:
        return None
    booking = db.get(Booking, notification.booking_id)
    user = db.get(User, notification.user_id)
    if booking is None or user is None:
        return None

    previous = db.get(Booking, booking.rescheduled_from_id) if kind == "moved" and booking.rescheduled_from_id else None
    facts = _facts(db, booking, user.id, previous)
    service, short, link = facts.service, facts.when.short, booking_url(booking.id)
    summary = f"{facts.service} with {facts.resource}"
    soon = notification.title.partition("starts in ")[2]  # "24 hours" in "Reminder: your booking starts in 24 hours"

    headings = {
        "confirmed": ("Your booking is confirmed", f"Booking confirmed: {service}, {short}"),
        "promoted": ("A place opened up: you're booked", f"You're booked: {service}, {short}"),
        "moved": ("Your booking has moved", f"Booking moved: {service}, now {short}"),
        "cancelled": ("Your booking was cancelled", f"Booking cancelled: {service}, {short}"),
        "reminder": (
            f"Your booking starts in {soon}" if soon else notification.title,
            f"Reminder: {service}, {short}" + (f" (in {soon})" if soon else ""),
        ),
        "received": (
            {BookingStatus.PENDING: "We received your booking", BookingStatus.WAITLISTED: "You are on the waitlist"}.get(
                booking.status, "Your booking was created"
            ),
            f"Booking received: {service}, {short}",
        ),
    }
    heading, subject = headings[kind]
    confirmed = booking.status == BookingStatus.CONFIRMED
    intro = ""
    if kind == "received" and booking.status == BookingStatus.PENDING:
        intro = "It is not confirmed yet. We will e-mail you as soon as it is."
    elif kind == "received" and booking.status == BookingStatus.WAITLISTED:
        intro = "We will book you in and e-mail you if a place opens up."
    elif kind == "moved" and confirmed:
        intro = "The new time replaces the old one in your calendar."
    elif kind == "cancelled":
        intro = "It comes off your calendar too." if booking.confirmed_at else ""
        if booking.cancellation_reason:
            intro = f"{intro} Reason: {booking.cancellation_reason}".strip()

    settings = get_settings()
    calendar = None
    notes: list[str] = []
    if kind in ("confirmed", "promoted", "moved") and confirmed:
        uid, revision = calendar_uid(db, booking)
        details = f"{summary}\nBooked at {facts.business}.\nView, change or cancel the booking: {link}"
        calendar = (
            ics.build(
                method="REQUEST", uid=uid, sequence=revision, start=booking.start_datetime, end=booking.end_datetime,
                stamp=utcnow(), created=booking.created_at, summary=summary, description=details, location=facts.place,
                url=link, organizer=(facts.business, settings.smtp_from), attendee=(user.name, user.email),
            ),
            "REQUEST",
        )
        notes = [ANSWER_YES + (" " + FOLLOWS if not revision else ""), NOT_THE_BOOKING]
    elif kind == "cancelled" and booking.confirmed_at:
        # Only a booking that was confirmed was ever sent to the customer's calendar.
        uid, revision = calendar_uid(db, booking)
        calendar = (
            ics.build(
                method="CANCEL", uid=uid, sequence=revision + 1, start=booking.start_datetime, end=booking.end_datetime,
                stamp=utcnow(), created=booking.created_at, summary=summary, description=f"Cancelled. {summary}",
                location=facts.place, url=link, organizer=(facts.business, settings.smtp_from), attendee=(user.name, user.email),
            ),
            "CANCEL",
        )

    lines = [heading, ""] + ([intro, ""] if intro else []) + _text_details(facts) + ["", f"View your booking: {link}"]
    lines += ["", *notes] if notes else []
    html = _page(
        business=facts.business,
        heading=heading,
        intro=intro,
        card=_card(facts, struck=kind == "cancelled"),
        buttons=[_button(link, "View booking")],
        notes=[escape(note) for note in notes],
        footer=f"You are receiving this because a booking was made for {user.email} at {facts.business}.",
    )
    return BookingMail(subject=subject, text="\n".join(lines), html=html, calendar=calendar)


def invitation_mail(db: Session, invitation: BookingInvitation, inviter: User, link: str, was: Booking | None = None) -> BookingMail | None:
    """The e-mail asking someone without an account to confirm a booking made for them
    (`was`: the booking it replaces, when it has just been moved). None when the
    invitation has no booking held (an invitation from before bookings were held)."""
    booking = db.get(Booking, invitation.booking_id) if invitation.booking_id else None
    if booking is None:
        return None
    facts = _facts(db, booking, booking.user_id, was)
    facts.maker = None  # the e-mail is all about who booked it
    service, short = facts.service, facts.when.short
    until = f"{invitation.expires_at:%a %d %b %Y}"
    if was is not None:
        subject = f"{inviter.name} has moved {service} to {short}: please confirm"
        heading = "Your booking has moved"
        intro = f"{inviter.name} has moved your booking. It is now:"
    else:
        subject = f"{inviter.name} has booked {service} for you: {short}"
        heading = f"{inviter.name} booked {service} for you"
        intro = f"{inviter.name} has booked the following for you:"
    ask = "It is held for you. Create your account to confirm it; if it isn't for you, decline it so the time is released."

    text = "\n".join(
        [f"Hi,\n\n{intro}\n", *_text_details(facts), "", ask, f"Confirm or decline it here (valid until {until}):", link]
    )
    html = _page(
        business=facts.business,
        heading=heading,
        intro=ask,
        card=_card(facts),
        buttons=[_button(link, "Confirm booking"), _button(link, "Decline", primary=False)],
        notes=[f"The link works until {escape(until)}."],
        footer=f"You are receiving this because {inviter.name} booked this for {invitation.email} at {facts.business}.",
    )
    return BookingMail(subject=subject, text=text, html=html)
