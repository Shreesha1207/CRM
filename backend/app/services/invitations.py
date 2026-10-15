"""Booking invitations: a booking an admin makes for someone with no account yet.

The booking is only made once the invitee registers and accepts; until then
nothing is held. A decline (or a booking that can no longer be made) is
reported to the admin who sent the invitation.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutils import get_zone, utcnow
from app.models import BookingInvitation, Resource, Service, User
from app.models.enums import NotificationType
from app.schemas import BookingCreateIn
from app.services import settings_service
from app.services.availability import resource_timezone
from app.services.notifications import _clock, notify, send_email


def expiry() -> datetime:
    return utcnow() + timedelta(days=get_settings().invitation_ttl_days)


def request_of(invitation: BookingInvitation) -> BookingCreateIn:
    return BookingCreateIn(**invitation.request)


def describe(db: Session, invitation: BookingInvitation) -> dict[str, str | None]:
    """The invited booking in words: service, resource (None = first free one) and time."""
    body = request_of(invitation)
    service = db.get(Service, body.service_id)
    resource = db.get(Resource, body.resource_id) if body.resource_id else None
    rules = settings_service.get_rules(db)
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
    lines = [
        f"Hi,\n\n{inviter.name} would like to book the following for you:\n",
        f"  Service: {details['service_name']}",
        f"  With: {details['resource_name'] or 'the first available'}",
        f"  When: {details['when']}",
        "\nYou don't have an account yet. Create one to accept the booking, or decline it, here "
        f"(valid until {invitation.expires_at:%a %d %b %Y}):",
        link(token),
        "\nIf you were not expecting this, you can ignore this e-mail.",
    ]
    send_email(invitation.email, f"{inviter.name} would like to book {details['service_name']} for you", "\n".join(lines))


def tell_inviter(
    db: Session, invitation: BookingInvitation, *, type: NotificationType, title: str, body: str, booking_id=None
) -> None:
    notify(db, user_id=invitation.invited_by_id, type=type, title=title, body=body, booking_id=booking_id)
