"""The e-mails the platform sends, all in one style.

Booking e-mails are written to be useful on their own: what was booked, when,
where (with a map button), a link to the booking, and a calendar invitation that
puts the booking in their calendar, moves it when the booking moves and takes it
out when the booking is cancelled. Every other e-mail (password links, help
requests, refunds, security notices...) uses the same page, without a calendar
entry. Nothing is ever sent as plain text only: the text part is the fallback.

Rendered when the e-mail is sent, from the booking as it is then.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.maps import google_maps_url
from app.core.permissions import is_staff
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
    NotificationType.BOOKING_UPDATED: "updated",
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


def facts(db: Session, booking: Booking, customer_id, previous: Booking | None = None) -> Facts:
    """What the booking e-mails show about this booking (the chat's booking card shows the same)."""
    return _facts(db, booking, customer_id, previous)


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
        "updated": ("Your booking has changed", f"Booking updated: {service}, {short}"),
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
    elif kind == "updated":
        intro = f"It is now for {booking.quantity} {'place' if booking.quantity == 1 else 'places'}. The time stays the same."
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


def welcome_mail(db: Session, user: User) -> BookingMail:
    """Sent once, when a new account is ready to use."""
    business = settings_service.get_rules(db).business_name
    base = get_settings().frontend_url
    first = user.name.split()[0] if user.name and "@" not in user.name else "there"
    steps = [
        ("Book", "Pick a service, then who or what and a time that suits you."),
        ("Keep track", "Your bookings are under My bookings, where you can move or cancel them."),
        ("Stay informed", "We e-mail a confirmation and reminders, each with a calendar invitation."),
    ]
    card = (
        '<div style="background:#ffffff;border:1px solid #e4e1d9;border-radius:12px;padding:20px">'
        + "".join(
            f'<div style="{"margin-top:14px;" if i else ""}font-size:15px;font-weight:600">{escape(title)}</div>'
            f'<div style="{MUTED}font-size:14px">{escape(body)}</div>'
            for i, (title, body) in enumerate(steps)
        )
        + "</div>"
    )
    html = _page(
        business=business,
        heading=f"Welcome to {business}, {first}",
        intro=f"Your account ({user.email}) is ready.",
        card=card,
        buttons=[_button(f"{base}/book", "Make a booking"), _button(f"{base}/dashboard", "My bookings", primary=False)],
        notes=[],
        footer=f"You get this e-mail because an account was created for {user.email}. If that wasn't you, reply to let us know.",
    )
    text = "\n".join(
        [
            f"Welcome to {business}, {first}!",
            "",
            f"Your account ({user.email}) is ready.",
            "",
            *(f"- {title}: {body}" for title, body in steps),
            "",
            f"Make a booking: {base}/book",
            f"My bookings: {base}/dashboard",
            "",
            f"If you didn't create an account with {business}, reply to let us know.",
        ]
    )
    return BookingMail(subject=f"Welcome to {business}", text=text, html=html)


# ---------------------------------------------------------------- every other e-mail


_URL = re.compile(r"https?://[^\s<>\"]+")


def _paragraphs(body: str) -> str:
    """Plain text as HTML: paragraphs, line breaks kept, and links that can be clicked."""

    def lines(text: str) -> str:
        return "<br>".join(_URL.sub(lambda m: f'<a href="{m.group(0)}" style="color:{ACCENT}">{m.group(0)}</a>', escape(line)) for line in text.split("\n"))

    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", body.strip()) if b.strip()]
    return "".join(f'<p style="margin:{"12px" if i else "0"} 0 0;font-size:15px;line-height:1.5">{lines(b)}</p>' for i, b in enumerate(blocks))


def notice_mail(
    *,
    business: str,
    subject: str,
    heading: str,
    body: str,
    footer: str,
    intro: str = "",
    button: tuple[str, str] | None = None,
    secondary: tuple[str, str] | None = None,
    card: str | None = None,
) -> BookingMail:
    """An e-mail that isn't about one booking's time, in the booking e-mails' style: the text
    in a card (or `card`, trusted HTML), and a button. No calendar entry. `button` is (link, label)."""
    content = f'<div style="background:#ffffff;border:1px solid #e4e1d9;border-radius:12px;padding:20px">{_paragraphs(body)}</div>' if body.strip() else ""
    buttons = [_button(*button)] if button else []
    if secondary:
        buttons.append(_button(*secondary, primary=False))
    html = _page(
        business=business,
        heading=heading,
        intro=intro,
        card=content + (f'<div style="margin-top:12px">{card}</div>' if card else ""),
        buttons=buttons,
        notes=[],
        footer=footer,
    )
    text = [heading, "", *([intro, ""] if intro else []), body.strip()]
    text += ["", *(f"{label}: {link}" for link, label in filter(None, (button, secondary)))] if button or secondary else []
    return BookingMail(subject=subject, text="\n".join(text), html=html)


# The button of each kind of notice: where its link (Notification.link) leads.
BUTTONS = {
    NotificationType.CHAT_HELP_REQUESTED: "Open the request",
    NotificationType.CHAT_HELP_OVERDUE: "Open the request",
    NotificationType.CHAT_HELP_RECEIVED: "Open",
    NotificationType.CHAT_HELP_RESOLVED: "Open",
    NotificationType.AGENT_CONNECTED: "Review connected apps",
    NotificationType.PAYMENT_REFUND_DUE: "My bookings",
    NotificationType.PAYMENT_REFUNDED: "My bookings",
}


def notification_mail(db: Session, notification: Notification, user: User) -> BookingMail:
    """Any notification without an e-mail of its own (help requests, refunds, security
    notices, answers to invitations...), in the same style. The booking it is about, if any,
    is shown as the booking e-mails show it."""
    business = settings_service.get_rules(db).business_name
    base = get_settings().frontend_url.rstrip("/")
    staff = is_staff(user.role)
    button = None
    if notification.link:
        link = notification.link if notification.link.startswith("http") else f"{base}{notification.link}"
        button = (link, BUTTONS.get(notification.type, "Open"))
    booking = db.get(Booking, notification.booking_id) if notification.booking_id else None
    card = None
    if booking is not None:
        card = _card(_facts(db, booking, booking.user_id))
        if button is None:
            button = (f"{base}/admin/bookings?open={booking.id}" if staff else booking_url(booking.id), "View booking")
    footer = (
        f"You are receiving this as a member of staff at {business}."
        if staff
        else f"You are receiving this because you have an account at {business} ({user.email})."
    )
    return notice_mail(
        business=business, subject=notification.title, heading=notification.title, body=notification.body,
        footer=footer, button=button, card=card,
    )  # fmt: skip


def styled(subject: str, body: str, business: str | None = None) -> str:
    """The HTML of an e-mail a caller wrote as text only: never send plain text alone."""
    name = business or get_settings().app_name
    return notice_mail(business=name, subject=subject, heading=subject, body=body, footer=f"Sent by {name}.").html
