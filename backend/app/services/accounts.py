"""Accounts: choosing a password, and deleting an account.

Nobody else ever sets a password: an account an admin creates gets a link to set
one up, and an admin can only send someone a link to reset theirs, never pick it.

Anyone can delete their own account (the right to erasure under India's DPDP
Act, 2023). Its upcoming bookings are cancelled, then it goes with its booking
history. An admin may delete a customer account once it has been inactive for
`inactive_account_deletion_days`; a super admin may delete any account below them.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_token, new_token
from app.core.timeutils import utcnow
from app.db.session import SessionLocal
from app.models import Booking, PasswordResetToken, User
from app.models.enums import BookingStatus, Role, UserStatus
from app.services import audit, booking_mail, settings_service
from app.services.booking_engine import BookingEngine
from app.services.notifications import send_email

log = logging.getLogger("auth")

# Bookings that still hold someone's time.
UPCOMING_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.WAITLISTED, BookingStatus.CONFLICTED)


def issue_password_link(db: Session, user: User, *, ttl: timedelta, setup: bool = False) -> str:
    """A fresh link to choose a password; any earlier unused link stops working."""
    now = utcnow()
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    token = new_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_token(token), expires_at=now + ttl))
    db.flush()
    link = f"{get_settings().frontend_url}/reset-password?token={token}"
    return f"{link}&setup=1" if setup else link


def send_setup_link(db: Session, user: User, inviter: User) -> None:
    """E-mail an account an admin made the link to set it up (valid INVITATION_TTL_DAYS)."""
    days = get_settings().invitation_ttl_days
    link = issue_password_link(db, user, ttl=timedelta(days=days), setup=True)
    business = settings_service.get_rules(db).business_name
    mail = booking_mail.notice_mail(
        business=business,
        subject=f"Set up your {business} account",
        heading=f"Set up your {business} account",
        intro=f"Hi {user.name}, {inviter.name} created an account for you ({user.email}).",
        body=f"Choose your password to start using it. The link works for {days} days.",
        button=(link, "Choose your password"),
        footer="If you were not expecting this, you can ignore this e-mail.",
    )
    send_email(user.email, mail.subject, mail.text, html=mail.html, from_name=business)


def send_reset_link(db: Session, user: User) -> None:
    """E-mail a link to choose a new password (valid PASSWORD_RESET_TTL_MINUTES)."""
    minutes = get_settings().password_reset_ttl_minutes
    link = issue_password_link(db, user, ttl=timedelta(minutes=minutes))
    business = settings_service.get_rules(db).business_name
    mail = booking_mail.notice_mail(
        business=business,
        subject="Reset your password",
        heading="Reset your password",
        intro=f"Hi {user.name},",
        body=f"Use the button to choose a new password. The link works for {minutes} minutes.",
        button=(link, "Choose a new password"),
        footer="If you did not ask for this, ignore this e-mail: your password stays as it is.",
    )
    send_email(user.email, mail.subject, mail.text, html=mail.html, from_name=business)


def set_status(user: User, status: UserStatus) -> None:
    if user.status != status:
        user.status = status
        user.status_changed_at = utcnow()


def upcoming_booking_ids(db: Session, user: User) -> list:
    return list(
        db.scalars(
            select(Booking.id)
            .where(Booking.user_id == user.id, Booking.status.in_(UPCOMING_STATUSES), Booking.end_datetime > utcnow())
            .order_by(Booking.start_datetime)
        )
    )


def deletable_from(db: Session, user: User) -> datetime | None:
    """When an admin (not only a super admin) may delete this account: a customer's,
    once it has been inactive for `inactive_account_deletion_days`. None = not by an admin."""
    days = settings_service.get_all(db)["inactive_account_deletion_days"]
    if user.role != Role.USER or user.status != UserStatus.INACTIVE or not days:
        return None
    return (user.status_changed_at or user.updated_at) + timedelta(days=days)


def erase(db: Session, user: User, *, actor_id, action: str, keep: dict | None = None) -> None:
    """Delete the account and its booking history. Upcoming bookings must be gone first."""
    removed = db.execute(delete(Booking).where(Booking.user_id == user.id)).rowcount
    audit.record(
        db, actor_id=actor_id, action=action, entity_type="user", entity_id=user.id,
        old={**(keep or {}), "role": user.role, "bookings_deleted": removed},
    )
    db.delete(user)


def close_own_account(db: Session, user: User) -> None:
    """The owner deletes their account: upcoming bookings are cancelled (whatever the
    cancellation window), then the account and its history are erased. Commits."""
    if user.role == Role.SUPER_ADMIN:
        others = db.scalar(
            select(func.count()).select_from(User).where(User.role == Role.SUPER_ADMIN, User.id != user.id, User.status == UserStatus.ACTIVE)
        )
        if not others:
            raise AppError(
                "LAST_SUPER_ADMIN",
                "You are the only super admin. Make another one on the server (python -m app.create_super_admin) first.",
                status_code=409,
            )
    engine = BookingEngine(db, user)
    upcoming = [db.get(Booking, booking_id) for booking_id in upcoming_booking_ids(db, user)]
    running = next((b for b in upcoming if b.start_datetime <= engine.now and b.status != BookingStatus.WAITLISTED), None)
    if running is not None:
        raise AppError(
            "BOOKING_IN_PROGRESS",
            f"Your {running.service.name} booking is happening now. You can delete your account once it has ended.",
            status_code=409,
        )
    for booking in upcoming:
        engine.cancel(booking.id, reason="The customer deleted their account", override_window=True)
    email, name, business = user.email, user.name, settings_service.get_rules(db).business_name
    user = db.get(User, user.id)
    # Nothing that identifies them is kept, not even in the audit log.
    erase(db, user, actor_id=None, action="ACCOUNT_DELETED_BY_OWNER")
    db.commit()
    try:
        mail = booking_mail.notice_mail(
            business=business,
            subject=f"Your {business} account has been deleted",
            heading="Your account has been deleted",
            intro=f"Hi {name},",
            body=f"As you asked, your {business} account ({email}) and its booking history have been deleted, "
            "and any upcoming bookings were cancelled.",
            footer="If you did not do this, reply to this e-mail.",
        )
        send_email(email, mail.subject, mail.text, html=mail.html, from_name=business)
    except Exception:
        log.exception("Could not send the account deletion e-mail")


def send_welcome(user_id) -> None:
    """E-mail a new account's owner a welcome, once the account is ready to use.

    Run after the response (a background task), so a slow mail server never holds
    up signing up; a failure is only logged."""
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return
        mail = booking_mail.welcome_mail(db, user)
        business = settings_service.get_rules(db).business_name
        try:
            send_email(user.email, mail.subject, mail.text, html=mail.html, from_name=business)
        except Exception:
            log.exception("Could not send the welcome e-mail to %s", user.email)
            return
        audit.record(db, actor_id=None, action="WELCOME_EMAIL_SENT", entity_type="user", entity_id=user.id)
        db.commit()
