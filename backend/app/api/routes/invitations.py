"""The invitee's side of a booking invitation. The token in the e-mailed link is
the credential, so these routes need no sign-in (except for an existing account).

The invited booking is already on the calendar, held as pending. Accepting
claims the account and confirms it; declining cancels it.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import DB, OptionalUser, rate_limit
from app.api.routes.auth import _start_session
from app.api.routes.bookings import create_with_auto_assign
from app.core.errors import AppError, BookingRejected, NotFound
from app.core.security import hash_password, hash_token
from app.core.timeutils import utcnow
from app.jobs.runner import dispatch_notifications
from app.models import Booking, BookingInvitation, User
from app.models.enums import BookingStatus, InvitationStatus, NotificationType, Role, UserStatus
from app.schemas import InvitationAcceptIn, InvitationAcceptOut, InvitationPublicOut
from app.services import accounts, audit, invitations
from app.services.booking_engine import BookingEngine

router = APIRouter(prefix="/api/invitations", tags=["invitations"], dependencies=[Depends(rate_limit("invitation", 30, 300))])


def _load(db: DB, token: str, *, lock: bool = False) -> BookingInvitation:
    stmt = select(BookingInvitation).where(BookingInvitation.token_hash == hash_token(token))
    invitation = db.scalar(stmt.with_for_update() if lock else stmt)
    if invitation is None:
        raise NotFound("Invitation")
    return invitation


def _load_by_id(db: DB, invitation_id: uuid.UUID) -> BookingInvitation:
    return db.scalars(select(BookingInvitation).where(BookingInvitation.id == invitation_id).with_for_update()).one()


def _state(invitation: BookingInvitation) -> str:
    if invitation.status == InvitationStatus.PENDING and invitation.expires_at <= utcnow():
        return "EXPIRED"
    return invitation.status.value


def _require_open(invitation: BookingInvitation) -> None:
    state = _state(invitation)
    if state != "PENDING":
        message = {
            "EXPIRED": "This invitation has expired",
            "WITHDRAWN": "This invitation was replaced by a newer one because the booking changed: use the link in the latest e-mail",
        }.get(state, "This invitation has already been answered")
        raise AppError("INVITATION_CLOSED", message, status_code=status.HTTP_410_GONE)


@router.get("/{token}", response_model=InvitationPublicOut)
def get_invitation(token: str, db: DB) -> InvitationPublicOut:
    invitation = _load(db, token)
    inviter = db.get(User, invitation.invited_by_id)
    details = invitations.describe(db, invitation)
    account = db.scalar(select(User).where(User.email == invitation.email))
    return InvitationPublicOut(
        email=invitation.email,
        status=_state(invitation),
        invited_by=inviter.name if inviter else "Someone",
        service_name=details["service_name"],
        resource_name=details["resource_name"],
        when=details["when"],
        expires_at=invitation.expires_at,
        # A placeholder made by the invitation is not an account yet.
        has_account=account is not None and account.status != UserStatus.INVITED,
        booked=invitation.booking_id is not None,
    )


@router.post("/{token}/decline")
def decline_invitation(token: str, db: DB, background: BackgroundTasks) -> dict:
    invitation = _load(db, token, lock=True)
    _require_open(invitation)
    inviter = db.get(User, invitation.invited_by_id)
    details = invitations.describe(db, invitation)
    invitation_id = invitation.id
    if invitation.booking_id:
        try:
            # Off the calendar, and the time is free for someone else.
            BookingEngine(db, inviter).cancel(invitation.booking_id, reason="Declined by the customer")
        except AppError:
            db.rollback()  # already cancelled (or finished): there is nothing left to remove
        invitation = _load_by_id(db, invitation_id)
    invitation.status = InvitationStatus.DECLINED
    invitation.responded_at = utcnow()
    invitations.tell_inviter(
        db,
        invitation,
        type=NotificationType.INVITATION_DECLINED,
        title="Booking rejected",
        body=f"{invitation.email} declined the booking for {details['service_name']} on {details['when']}."
        + (" It was removed from the calendar." if invitation.booking_id else ""),
    )
    audit.record(db, actor_id=None, action="BOOKING_INVITATION_DECLINED", entity_type="booking_invitation", entity_id=invitation_id)
    db.commit()
    background.add_task(dispatch_notifications)
    return {"message": "You declined the booking. We have let them know."}


@router.post("/{token}/accept", response_model=InvitationAcceptOut)
def accept_invitation(
    token: str,
    body: InvitationAcceptIn,
    request: Request,
    response: Response,
    db: DB,
    background: BackgroundTasks,
    current: OptionalUser,
) -> InvitationAcceptOut:
    invitation = _load(db, token, lock=True)
    _require_open(invitation)
    user = db.scalar(select(User).where(User.email == invitation.email))
    signed_in = False
    if user is None or user.status == UserStatus.INVITED:
        if not body.name or not body.password:
            raise AppError("DETAILS_REQUIRED", "Enter your name and choose a password", status_code=422)
        if user is None:  # an invitation from before bookings were held
            user = User(email=invitation.email, role=Role.USER)
            db.add(user)
        user.name = body.name.strip()
        user.phone = body.phone
        user.password_hash = hash_password(body.password)
        accounts.set_status(user, UserStatus.ACTIVE)
        db.flush()
        audit.record(
            db, actor_id=user.id, action="USER_REGISTERED", entity_type="user", entity_id=user.id,
            new={"via": "booking invitation"},
        )
        # The account is kept (and the customer signed in) whatever happens to the booking.
        _start_session(db, user, request, response)
        signed_in = True
    elif user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not active")
    elif current is None or current.id != user.id:
        raise AppError("SIGN_IN_REQUIRED", f"Sign in as {invitation.email} to accept this booking", status_code=401)

    # Creating the account committed, which released the row lock: take it again so a
    # double click cannot confirm twice.
    invitation_id = invitation.id
    invitation = _load_by_id(db, invitation_id)
    _require_open(invitation)
    inviter = db.get(User, invitation.invited_by_id)
    details = invitations.describe(db, invitation)
    what = f"{details['service_name']} on {details['when']}"
    try:
        engine = BookingEngine(db, inviter)
        if invitation.booking_id:
            booking = db.get(Booking, invitation.booking_id)
            if booking is None or booking.status == BookingStatus.CANCELLED:
                raise BookingRejected("BOOKING_CANCELLED", "This booking was cancelled", status_code=409)
            if booking.status == BookingStatus.PENDING:
                engine.confirm(booking.id, customer_accepted=True)  # sends the customer their confirmation
        else:
            booking = create_with_auto_assign(
                engine, invitations.request_of(invitation), user_id=user.id,
                override_rules=bool(invitation.request.get("override_rules")),
            )
    except AppError as exc:
        db.rollback()
        invitation = _load_by_id(db, invitation_id)
        invitation.status = InvitationStatus.FAILED
        invitation.responded_at = utcnow()
        invitation.failure_reason = exc.message
        invitations.tell_inviter(
            db,
            invitation,
            type=NotificationType.INVITATION_ACCEPTED,
            title="Invitation accepted, but the booking failed",
            body=f"{invitation.email} registered, but {what} could not be confirmed: {exc.message}.",
        )
        db.commit()
        background.add_task(dispatch_notifications)
        return InvitationAcceptOut(booking_id=None, error=exc.message, signed_in=signed_in)

    invitation = _load_by_id(db, invitation_id)
    invitation.status = InvitationStatus.ACCEPTED
    invitation.responded_at = utcnow()
    invitation.booking_id = booking.id
    invitations.tell_inviter(
        db,
        invitation,
        type=NotificationType.INVITATION_ACCEPTED,
        title="Invitation accepted",
        body=f"{invitation.email} accepted and is booked for {what}.",
        booking_id=booking.id,
    )
    db.commit()
    background.add_task(dispatch_notifications)
    return InvitationAcceptOut(booking_id=booking.id, signed_in=signed_in)
