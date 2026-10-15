"""Booking invitations: a booking an admin makes for someone with no account yet.

The booking goes on the calendar at once, held as pending under a placeholder
account (status INVITED). Accepting claims the account and confirms the
booking; declining cancels it, which takes it off the calendar. If the invitee
never answers, it stays as it is. The admin who sent the invitation is told how
it was answered.

Invitations made before bookings were held (no booking_id) still work the old
way: the booking is only made when the invitee accepts.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import get_zone, utcnow
from app.models import Booking, BookingInvitation, Resource, Service, User
from app.models.enums import InvitationStatus, NotificationType
from app.schemas import BookingCreateIn
from app.services import settings_service
from app.services.availability import resource_timezone
from app.services.booking_engine import BookingEngine
from app.services.notifications import _clock, notify, send_email


def expiry() -> datetime:
    return utcnow() + timedelta(days=get_settings().invitation_ttl_days)


def request_of(invitation: BookingInvitation) -> BookingCreateIn:
    return BookingCreateIn(**invitation.request)


def describe(db: Session, invitation: BookingInvitation) -> dict[str, str | None]:
    """The invited booking in words: service, resource (None = first free one) and time.

    Read from the booking itself when there is one, so a time an admin changed
    afterwards is what the customer sees."""
    rules = settings_service.get_rules(db)
    booking = db.get(Booking, invitation.booking_id) if invitation.booking_id else None
    if booking is not None:
        service, resource = booking.service, booking.primary_resource
        zone = resource_timezone(resource, rules)
        start = booking.start_datetime.astimezone(zone)
    else:
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


def link(token: str) -> str:
    return f"{get_settings().frontend_url}/invitation?token={token}"


def send_invitation(db: Session, invitation: BookingInvitation, inviter: User, token: str) -> None:
    details = describe(db, invitation)
    held = invitation.booking_id is not None
    lines = [
        f"Hi,\n\n{inviter.name} has {'booked' if held else 'asked to book'} the following for you:\n",
        f"  Service: {details['service_name']}",
        f"  With: {details['resource_name'] or 'the first available'}",
        f"  When: {details['when']}",
        ("\nIt is on the calendar for you now. " if held else "\n")
        + "You don't have an account yet. Create one to confirm the booking, or decline it, here "
        f"(valid until {invitation.expires_at:%a %d %b %Y}):",
        link(token),
        "\nIf this is not for you, please decline it" + (" so the time is released." if held else " or ignore this e-mail."),
    ]
    subject = f"{inviter.name} has booked {details['service_name']} for you" if held else f"{inviter.name} would like to book {details['service_name']} for you"
    send_email(invitation.email, subject, "\n".join(lines))


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


def tell_inviter(
    db: Session, invitation: BookingInvitation, *, type: NotificationType, title: str, body: str, booking_id=None
) -> None:
    notify(db, user_id=invitation.invited_by_id, type=type, title=title, body=body, booking_id=booking_id)
