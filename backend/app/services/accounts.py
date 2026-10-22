"""Links that let people choose their own password.

Nobody else ever sets a password: an account an admin creates gets a link to set
one up, and an admin can only send someone a link to reset theirs, never pick it.
"""

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token, new_token
from app.core.timeutils import utcnow
from app.models import PasswordResetToken, User
from app.services import settings_service
from app.services.notifications import send_email


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
    send_email(
        user.email,
        f"Set up your {business} account",
        f"Hi {user.name},\n\n{inviter.name} created a {business} account for you ({user.email}).\n\n"
        f"Choose your password here to start using it (the link works for {days} days):\n{link}\n\n"
        "If you were not expecting this, you can ignore this e-mail.",
        from_name=business,
    )


def send_reset_link(db: Session, user: User) -> None:
    """E-mail a link to choose a new password (valid PASSWORD_RESET_TTL_MINUTES)."""
    minutes = get_settings().password_reset_ttl_minutes
    link = issue_password_link(db, user, ttl=timedelta(minutes=minutes))
    send_email(
        user.email,
        "Reset your password",
        f"Hi {user.name},\n\nUse this link to choose a new password "
        f"(valid for {minutes} minutes):\n{link}\n\n"
        "If you did not ask for this, ignore this e-mail.",
    )
