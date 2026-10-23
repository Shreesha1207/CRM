"""Booking invitations: a booking an admin makes for someone with no account yet.

The booking goes on the calendar at once, held as pending under a placeholder
account (status INVITED). Accepting claims the account and confirms the
booking; declining cancels it, which takes it off the calendar. If the invitee
never answers, it stays as it is. The admin who sent the invitation is told how
it was answered.

If an admin moves a booking that is still waiting for its customer, the old
time is cancelled and the customer gets a new link for the new time: only they
can confirm it, never the admin.

Invitations made before bookings were held (no booking_id) still work the old
way: the booking is only made when the invitee accepts.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_token, new_token
from app.core.timeutils import get_zone, utcnow
from app.models import Booking, BookingInvitation, Resource, Service, User
from app.models.enums import InvitationStatus, NotificationType
from app.schemas import BookingCreateIn
from app.services import audit, booking_mail, events, settings_service
from app.services.availability import resource_timezone
from app.services.booking_engine import BookingEngine
from app.services.notifications import _clock, notify, send_email

log = logging.getLogger("bookings")
# Links to send once the request that made them has committed (kept on the database session).
MAIL_QUEUE = "invitation_emails"


def expiry() -> datetime:
    return utcnow() + timedelta(days=get_settings().invitation_ttl_days)


def request_of(invitation: BookingInvitation) -> BookingCreateIn:
    return BookingCreateIn(**invitation.request)


def describe(db: Session, invitation: BookingInvitation) -> dict[str, str | None]:
    """The invited booking in words: service, resource (None = first free one) and time.

    Read from the booking itself when there is one, so a time an admin changed
    afterwards is what the customer sees."""
    booking = db.get(Booking, invitation.booking_id) if invitation.booking_id else None
    if booking is not None:
        return {
            "service_name": booking.service.name,
            "resource_name": booking.primary_resource.name,
            "when": booking_when(db, booking),
        }
    # An invitation from before bookings were held: only the request exists.
    rules = settings_service.get_rules(db)
    body = request_of(invitation)
    service = db.get(Service, body.service_id)
    resource = db.get(Resource, body.resource_id) if body.resource_id else None
    zone = resource_timezone(resource, rules) if resource else get_zone(rules.default_timezone)
    start = body.start.astimezone(zone) if body.start.tzinfo else body.start
    when = f"{start:%a %d %b %Y}, {_clock(start)}"
    return {
        "service_name": service.name if service else "a service",
        "resource_name": resource.name if resource else None,
        "when": f"{when} ({zone.key})",
    }


def booking_when(db: Session, booking: Booking) -> str:
    """"Fri 16 Oct 2026, 2:00 PM (Asia/Kolkata)" for a booking's start."""
    zone = resource_timezone(booking.primary_resource, settings_service.get_rules(db))
    start = booking.start_datetime.astimezone(zone)
    return f"{start:%a %d %b %Y}, {_clock(start)} ({zone.key})"


def link(token: str) -> str:
    return f"{get_settings().frontend_url}/invitation?token={token}"


def send_invitation(db: Session, invitation: BookingInvitation, inviter: User, token: str, was: Booking | None = None) -> None:
    """E-mail the link. `was` is the booking it replaces, when it has just been moved."""
    mail = booking_mail.invitation_mail(db, invitation, inviter, link(token), was)
    if mail is not None:
        business = settings_service.get_rules(db).business_name
        send_email(invitation.email, mail.subject, mail.text, html=mail.html, from_name=business)
        return
    # An invitation from before bookings were held: nothing is booked until they accept.
    details = describe(db, invitation)
    business = settings_service.get_rules(db).business_name
    mail = booking_mail.notice_mail(
        business=business,
        subject=f"{inviter.name} would like to book {details['service_name']} for you",
        heading=f"{inviter.name} would like to book {details['service_name']} for you",
        body=f"{details['service_name']} with {details['resource_name'] or 'the first available'}\n{details['when']}\n\n"
        "You don't have an account yet. Create one to confirm the booking, or decline it "
        f"(the link works until {invitation.expires_at:%a %d %b %Y}).",
        button=(link(token), "Confirm or decline"),
        footer="If this is not for you, please decline it or ignore this e-mail.",
    )
    send_email(invitation.email, mail.subject, mail.text, html=mail.html, from_name=business)


def withdraw(db: Session, invitation: BookingInvitation, actor: User, reason: str) -> None:
    """Take an invitation's booking off the calendar again and close the invitation."""
    if invitation.booking_id:
        try:
            BookingEngine(db, actor).cancel(invitation.booking_id, reason=reason)
        except Exception:  # already cancelled or gone: nothing left to remove
            db.rollback()
    invitation = db.get(BookingInvitation, invitation.id)
    invitation.status = InvitationStatus.FAILED
    invitation.responded_at = utcnow()
    invitation.failure_reason = reason
    db.commit()


def _on_rescheduled(db: Session, event: events.DomainEvent) -> None:
    """A booking that waits for its customer was moved: the old link dies, a new one is made."""
    previous: Booking = event.payload["previous"]
    booking: Booking = event.payload["booking"]
    old = db.scalar(
        select(BookingInvitation).where(
            BookingInvitation.booking_id == previous.id, BookingInvitation.status == InvitationStatus.PENDING
        )
    )
    if old is None:
        return
    actor_id = event.payload.get("actor_id") or old.invited_by_id
    token = new_token()
    old.status = InvitationStatus.WITHDRAWN
    old.responded_at = utcnow()
    fresh = BookingInvitation(
        email=old.email,
        token_hash=hash_token(token),
        invited_by_id=actor_id,
        request=old.request,
        booking_id=booking.id,
        expires_at=expiry(),
    )
    db.add(fresh)
    db.flush()
    audit.record(
        db, actor_id=actor_id, action="BOOKING_INVITATION_SENT", entity_type="booking_invitation", entity_id=fresh.id,
        new={"email": old.email, "booking_id": booking.id, "reason": "booking moved"},
    )
    # The e-mail goes out once the move has been committed (see send_queued).
    db.info.setdefault(MAIL_QUEUE, []).append((fresh.id, token, previous.id))


def register_handlers() -> None:
    events.subscribe("booking.rescheduled", _on_rescheduled)


def send_queued(db: Session) -> None:
    """E-mail the new links made while a booking was moved. Call after the move has committed.

    The move stands even if the e-mail fails; the error tells the admin to resend."""
    failed = []
    for invitation_id, token, previous_id in db.info.pop(MAIL_QUEUE, []):
        invitation = db.get(BookingInvitation, invitation_id)
        try:
            send_invitation(db, invitation, db.get(User, invitation.invited_by_id), token, was=db.get(Booking, previous_id))
        except Exception:
            log.exception("Could not send the moved-booking invitation to %s", invitation.email)
            failed.append(invitation.email)
    if failed:
        raise AppError(
            "EMAIL_FAILED",
            f"The booking was moved, but the e-mail to {', '.join(failed)} could not be sent. Open the booking and press Resend invitation.",
            status_code=502,
        )


def reissue(db: Session, invitation: BookingInvitation, actor: User) -> BookingInvitation:
    """A fresh link for an invitation that is still open (also when its time ran out). The old link stops working."""
    token = new_token()
    invitation.token_hash = hash_token(token)
    invitation.expires_at = expiry()
    invitation.invited_by_id = actor.id
    db.flush()
    try:
        send_invitation(db, invitation, actor, token)
    except Exception:
        log.exception("Could not resend the booking invitation to %s", invitation.email)
        db.rollback()  # the old link keeps working
        raise AppError("EMAIL_FAILED", f"The invitation e-mail to {invitation.email} could not be sent", status_code=502) from None
    audit.record(
        db, actor_id=actor.id, action="BOOKING_INVITATION_RESENT", entity_type="booking_invitation",
        entity_id=invitation.id, new={"email": invitation.email},
    )
    db.commit()
    return invitation


def awaiting_customer(db: Session, booking_ids: list) -> set:
    """Which of these bookings are waiting for their customer to confirm."""
    if not booking_ids:
        return set()
    return set(
        db.scalars(
            select(BookingInvitation.booking_id).where(
                BookingInvitation.booking_id.in_(booking_ids), BookingInvitation.status == InvitationStatus.PENDING
            )
        )
    )


def tell_inviter(
    db: Session, invitation: BookingInvitation, *, type: NotificationType, title: str, body: str, booking_id=None
) -> None:
    notify(db, user_id=invitation.invited_by_id, type=type, title=title, body=body, booking_id=booking_id)
