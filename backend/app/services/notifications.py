"""Notifications, kept separate from booking logic.

The booking engine only publishes domain events. This module subscribes to
them, records in-app notifications and queues e-mails in the same database
transaction, and a background dispatcher delivers queued messages later.
"""

import logging
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.maps import google_maps_url
from app.core.timeutils import utcnow
from app.models import Booking, Notification, User
from app.models.enums import (
    BookingStatus,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)
from app.services import events, settings_service
from app.services.availability import resource_timezone
from app.services.events import DomainEvent

log = logging.getLogger("notifications")

# Channels each notification is delivered on. SMS / push senders are
# pluggable; they are skipped until a provider is configured.
DEFAULT_CHANNELS = (NotificationChannel.IN_APP, NotificationChannel.EMAIL)


def _when(db: Session, booking: Booking) -> str:
    rules = settings_service.get_rules(db)
    tz = resource_timezone(booking.primary_resource, rules)
    start = booking.start_datetime.astimezone(tz)
    end = booking.end_datetime.astimezone(tz)
    if end.date() != start.date():  # past midnight: the end gets its own date
        return f"{start:%a %d %b %Y, %H:%M} – {end:%a %d %b %Y, %H:%M} ({tz.key})"
    return f"{start:%a %d %b %Y, %H:%M}–{end:%H:%M} ({tz.key})"


def notify(
    db: Session,
    *,
    user_id,
    type: NotificationType,
    title: str,
    body: str,
    booking_id=None,
    dedupe_key: str | None = None,
    channels=DEFAULT_CHANNELS,
    email_body: str | None = None,
) -> None:
    now = utcnow()
    for channel in channels:
        values = dict(
            user_id=user_id,
            booking_id=booking_id,
            type=type,
            channel=channel,
            title=title,
            body=email_body if email_body is not None and channel == NotificationChannel.EMAIL else body,
            # In-app messages are "delivered" by being stored.
            status=NotificationStatus.SENT if channel == NotificationChannel.IN_APP else NotificationStatus.PENDING,
            sent_at=now if channel == NotificationChannel.IN_APP else None,
            dedupe_key=f"{dedupe_key}:{channel.value}" if dedupe_key else None,
        )
        if dedupe_key:
            # ON CONFLICT DO NOTHING: a reminder can never be queued twice,
            # even if two job runners race.
            stmt = insert(Notification).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"])
            db.execute(stmt)
        else:
            db.add(Notification(**values))


def _booking_line(db: Session, booking: Booking) -> str:
    return f"{booking.service.name} with {booking.primary_resource.name}, {_when(db, booking)}"


def _with_place(booking: Booking, body: str) -> dict[str, str]:
    """Message texts for a booking the customer is going to: where it is, and
    in the e-mail a Google Maps link to get there."""
    location = booking.primary_resource.location
    if location is None:
        return {"body": body}
    body += f"\nWhere: {location.name}" + (f", {location.address}" if location.address else "")
    url = google_maps_url(location.name, location.address, location.map_url)
    return {"body": body, "email_body": f"{body}\nMap: {url}" if url else body}


def _on_created(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    titles = {
        BookingStatus.CONFIRMED: (NotificationType.BOOKING_CONFIRMED, "Booking confirmed"),
        BookingStatus.PENDING: (NotificationType.BOOKING_CREATED, "Booking received – awaiting confirmation"),
        BookingStatus.WAITLISTED: (NotificationType.BOOKING_CREATED, "You are on the waitlist"),
    }
    type_, title = titles.get(booking.status, (NotificationType.BOOKING_CREATED, "Booking created"))
    notify(
        db, user_id=booking.user_id, booking_id=booking.id, type=type_, title=title, **_with_place(booking, _booking_line(db, booking))
    )


def _on_confirmed(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    notify(
        db,
        user_id=booking.user_id,
        booking_id=booking.id,
        type=NotificationType.BOOKING_CONFIRMED,
        title="Booking confirmed",
        **_with_place(booking, _booking_line(db, booking)),
    )


def _on_cancelled(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    by_someone_else = event.payload.get("actor_id") not in (None, booking.user_id)
    body = _booking_line(db, booking)
    if booking.cancellation_reason:
        body += f"\nReason: {booking.cancellation_reason}"
    notify(
        db,
        user_id=booking.user_id,
        booking_id=booking.id,
        type=NotificationType.BOOKING_CANCELLED,
        title="Your booking was cancelled" if by_someone_else else "Booking cancelled",
        body=body,
    )


def _on_rescheduled(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    changed = event.payload.get("resource_changed")
    notify(
        db,
        user_id=booking.user_id,
        booking_id=booking.id,
        type=NotificationType.RESOURCE_CHANGED if changed else NotificationType.BOOKING_RESCHEDULED,
        title="Your booking was moved" if changed else "Booking rescheduled",
        **_with_place(booking, f"New time: {_booking_line(db, booking)}"),
    )


def _on_promoted(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    notify(
        db,
        user_id=booking.user_id,
        booking_id=booking.id,
        type=NotificationType.WAITLIST_PROMOTION,
        title="A place opened up – you're booked!",
        **_with_place(booking, _booking_line(db, booking)),
    )


def _on_conflicted(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    notify(
        db,
        user_id=booking.user_id,
        booking_id=booking.id,
        type=NotificationType.BOOKING_CONFLICTED,
        title="Your booking needs attention",
        body=f"{_booking_line(db, booking)}\nA schedule change affects this booking. We will contact you to resolve it.",
        channels=(NotificationChannel.IN_APP,),
    )


def register_handlers() -> None:
    events.subscribe("booking.created", _on_created)
    events.subscribe("booking.confirmed", _on_confirmed)
    events.subscribe("booking.cancelled", _on_cancelled)
    events.subscribe("booking.rescheduled", _on_rescheduled)
    events.subscribe("booking.waitlist_promoted", _on_promoted)
    events.subscribe("booking.conflicted", _on_conflicted)


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def send_email(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if settings.email_backend == "smtp":
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_username:
                smtp.starttls()
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
    else:
        log.info("EMAIL to=%s subject=%r\n%s", to, subject, body)


def dispatch_pending(db: Session, limit: int = 100) -> int:
    """Deliver queued non-in-app notifications. Returns how many were processed."""
    rows = db.scalars(
        select(Notification)
        .where(
            Notification.status == NotificationStatus.PENDING,
            Notification.channel != NotificationChannel.IN_APP,
        )
        .order_by(Notification.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for notification in rows:
        user = db.get(User, notification.user_id)
        try:
            if notification.channel == NotificationChannel.EMAIL and user is not None:
                send_email(user.email, notification.title, notification.body)
                notification.status = NotificationStatus.SENT
                notification.sent_at = utcnow()
            else:
                notification.status = NotificationStatus.SKIPPED
        except Exception as exc:  # delivery errors must not break the loop
            log.exception("Notification %s failed", notification.id)
            notification.status = NotificationStatus.FAILED
            notification.error = str(exc)[:1000]
    db.commit()
    return len(rows)


def queue_reminders(db: Session, now: datetime) -> int:
    """Queue reminders that are due. Idempotent thanks to dedupe keys."""
    rules = settings_service.get_rules(db)
    if not rules.reminder_offsets:
        return 0
    horizon = now + timedelta(minutes=max(rules.reminder_offsets))
    bookings = db.scalars(
        select(Booking).where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_datetime > now,
            Booking.start_datetime <= horizon,
        )
    ).unique().all()
    queued = 0
    for booking in bookings:
        # Only the closest due offset: a booking made 30 minutes ahead gets
        # one "1 hour" reminder, not a late "24 hours" one as well.
        due = [m for m in rules.reminder_offsets if booking.start_datetime - timedelta(minutes=m) <= now]
        if not due:
            continue
        minutes = min(due)
        label = f"{minutes // 60} hour{'s' if minutes // 60 != 1 else ''}" if minutes % 60 == 0 else f"{minutes} minutes"
        notify(
            db,
            user_id=booking.user_id,
            booking_id=booking.id,
            type=NotificationType.BOOKING_REMINDER,
            title=f"Reminder: your booking starts in {label}",
            dedupe_key=f"reminder:{booking.id}:{minutes}",
            **_with_place(booking, _booking_line(db, booking)),
        )
        queued += 1
    db.commit()
    return queued
