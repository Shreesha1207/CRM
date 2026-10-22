"""Admin: users, settings, dashboard, reports and the audit log."""

import logging
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, update

from app.api.deps import DB
from app.api.deps import require as require_permission
from app.core.errors import AppError
from app.core.permissions import Permission, can_assign_role, can_manage_account
from app.core.security import hash_password, new_token
from app.core.timeutils import day_bounds, get_zone, utcnow
from app.models import AuditLog, AuthSession, Booking, User
from app.models.enums import BookingStatus, Role, UserStatus
from app.schemas import AuditLogOut, Page, SettingsIn, SettingsOut, UserCreateIn, UserOut, UserUpdateIn
from app.services import accounts, audit, reports, settings_service

router = APIRouter(prefix="/api/admin", tags=["admin: system"])
log = logging.getLogger("auth")

# Bookings that still hold someone's time.
UPCOMING_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.WAITLISTED, BookingStatus.CONFLICTED)


@router.get("/dashboard")
def dashboard(db: DB, _: User = Depends(require_permission(Permission.REPORTS_VIEW))) -> dict:
    return reports.dashboard(db, utcnow())


@router.get("/reports/utilization")
def utilization(
    db: DB, start: date, end: date, _: User = Depends(require_permission(Permission.REPORTS_VIEW))
) -> list[dict]:
    if end < start or (end - start) > timedelta(days=366):
        raise AppError("INVALID_RANGE", "Choose a range of at most one year", status_code=422)
    tz = get_zone(settings_service.get_rules(db).default_timezone)
    return reports.resource_utilization(db, day_bounds(start, tz)[0], day_bounds(end, tz)[1])


# ---------------------------------------------------------------- users


def _cannot_give(role: Role) -> str:
    if role == Role.SUPER_ADMIN:
        return "Nobody can be made a super admin from the app"
    return f"Only a super admin can give the {role.value.replace('_', ' ').lower()} role"


def _not_yours(user: User) -> str:
    if user.role == Role.SUPER_ADMIN:
        return "A super admin's account can only be changed by its owner"
    return "Only a super admin can change admin accounts"


@router.get("/users", response_model=Page[UserOut])
def list_users(
    db: DB,
    _: User = Depends(require_permission(Permission.USERS_VIEW)),
    q: str | None = Query(default=None, max_length=100),
    role: Role | None = None,
    status_filter: UserStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[UserOut]:
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%"), User.phone.ilike(f"%{q}%")))
    if role:
        stmt = stmt.where(User.role == role)
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[UserOut.model_validate(u) for u in rows], total=total)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreateIn, db: DB, actor: User = Depends(require_permission(Permission.USERS_MANAGE))) -> User:
    """Make an account for someone. They get an e-mail with a link to choose their own
    password; until they do, the account is INVITED and cannot sign in."""
    if not can_assign_role(actor.role, body.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _cannot_give(body.role))
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    user = User(
        name=body.name.strip(), email=email, phone=body.phone,
        # Nobody knows this password: the person chooses their own with the link.
        password_hash=hash_password(new_token()), role=body.role, status=UserStatus.INVITED,
    )
    db.add(user)
    db.flush()
    audit.record(db, actor_id=actor.id, action="USER_CREATED", entity_type="user", entity_id=user.id, new={"email": email, "role": body.role, "status": user.status})
    try:
        accounts.send_setup_link(db, user, actor)
    except Exception:
        log.exception("Could not send the account set-up e-mail to %s", email)
        db.rollback()  # nobody would know the account exists
        raise AppError("EMAIL_FAILED", f"The e-mail to {email} could not be sent, so the account was not created", status_code=502) from None
    db.commit()
    return user


@router.post("/users/{user_id}/send-password-link")
def send_password_link(user_id: uuid.UUID, db: DB, actor: User = Depends(require_permission(Permission.USERS_MANAGE))) -> dict:
    """E-mail someone a link to choose a password: to set up an invited account, or to
    reset a forgotten one. The admin never sees or sets the password."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id != actor.id and not can_manage_account(actor.role, user.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _not_yours(user))
    if user.status not in (UserStatus.ACTIVE, UserStatus.INVITED):
        raise AppError("ACCOUNT_NOT_ACTIVE", f"{user.name}'s account is {user.status.value.lower()}: make it active first", status_code=409)
    setup = user.status == UserStatus.INVITED
    try:
        if setup:
            accounts.send_setup_link(db, user, actor)
        else:
            accounts.send_reset_link(db, user)
    except Exception:
        log.exception("Could not send the password e-mail to %s", user.email)
        db.rollback()  # the earlier link keeps working
        raise AppError("EMAIL_FAILED", f"The e-mail to {user.email} could not be sent", status_code=502) from None
    audit.record(
        db, actor_id=actor.id, action="ACCOUNT_SETUP_LINK_SENT" if setup else "PASSWORD_RESET_LINK_SENT",
        entity_type="user", entity_id=user.id,
    )
    db.commit()
    if setup:
        return {"message": f"A new link to set up the account was sent to {user.email}."}
    return {"message": f"A link to choose a new password was sent to {user.email}."}


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID, body: UserUpdateIn, db: DB, actor: User = Depends(require_permission(Permission.USERS_MANAGE))
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = body.model_dump(exclude_unset=True)
    # Only accounts below you in the hierarchy are yours to change (your own aside).
    if user.id != actor.id and not can_manage_account(actor.role, user.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _not_yours(user))
    if "role" in changes and changes["role"] != user.role:
        if user.id == actor.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role")
        if not can_assign_role(actor.role, changes["role"]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, _cannot_give(changes["role"]))
    if changes.get("status") == UserStatus.INVITED and user.status != UserStatus.INVITED:
        raise HTTPException(422, "Accounts are only invited by booking for an e-mail address")
    if "status" in changes and changes["status"] != user.status:
        if user.id == actor.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change the status of your own account")
        # An invited account follows what the person does: it becomes active when they set it up.
        if user.status == UserStatus.INVITED:
            raise AppError(
                "STILL_INVITED",
                f"{user.name} has not set up their account yet. It becomes active when they accept the invitation",
                status_code=409,
            )

    old = audit.snapshot(user, "name", "phone", "role", "status")
    for key, value in changes.items():
        setattr(user, key, value)
    if changes.get("status", UserStatus.ACTIVE) not in (UserStatus.ACTIVE, UserStatus.INVITED):
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(db, actor_id=actor.id, action="USER_UPDATED", entity_type="user", entity_id=user.id, old=old, new=changes)
    db.commit()
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: uuid.UUID, db: DB, actor: User = Depends(require_permission(Permission.USERS_DELETE))) -> None:
    """Delete an account (customer or admin) and its past bookings for good.

    Refused while the account still has upcoming bookings, so nobody's time is
    freed without the waitlist and notifications that a cancellation brings."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if not can_manage_account(actor.role, user.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _not_yours(user))
    upcoming = db.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.user_id == user.id, Booking.status.in_(UPCOMING_STATUSES), Booking.end_datetime > utcnow())
    )
    if upcoming:
        raise AppError(
            "HAS_UPCOMING_BOOKINGS",
            f"{user.name} has {upcoming} upcoming booking{'s' if upcoming != 1 else ''}. Cancel them first, or deactivate the account instead.",
            status_code=409,
        )
    removed = db.execute(delete(Booking).where(Booking.user_id == user.id)).rowcount
    audit.record(
        db, actor_id=actor.id, action="USER_DELETED", entity_type="user", entity_id=user.id,
        old={**audit.snapshot(user, "name", "email", "role", "status"), "bookings_deleted": removed},
    )
    db.delete(user)
    db.commit()


# ---------------------------------------------------------------- settings


@router.get("/settings", response_model=SettingsOut)
def get_settings_values(db: DB, _: User = Depends(require_permission(Permission.SETTINGS_MANAGE))) -> SettingsOut:
    return _settings_out(db)


def _settings_out(db: DB) -> SettingsOut:
    return SettingsOut(
        values=settings_service.get_all(db), versions=settings_service.get_versions(db), definitions=settings_service.describe()
    )


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: SettingsIn, db: DB, actor: User = Depends(require_permission(Permission.SETTINGS_MANAGE))) -> SettingsOut:
    """Save only the settings in `values`. With `versions` (as loaded), a setting another
    admin saved in the meantime is refused (409) instead of silently overwritten."""
    old = settings_service.get_all(db)
    try:
        settings_service.update(db, body.values, actor.id, expected=body.versions)
    except settings_service.SettingsValidationError as exc:
        raise AppError("INVALID_SETTING", str(exc), status_code=422) from exc
    except settings_service.SettingsConflict as exc:
        db.rollback()
        names = ", ".join(k.replace("_", " ") for k in exc.keys)
        raise AppError(
            "SETTINGS_CHANGED",
            f"Someone else changed {names} while you were editing. Their values are shown now: check them and save again.",
            status_code=409,
            details={"keys": exc.keys},
        ) from exc
    audit.record(
        db, actor_id=actor.id, action="SETTINGS_CHANGED", entity_type="settings",
        old={k: old[k] for k in body.values}, new=body.values,
    )
    db.commit()
    return _settings_out(db)


# ---------------------------------------------------------------- audit


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def audit_logs(
    db: DB,
    _: User = Depends(require_permission(Permission.AUDIT_VIEW)),
    action: str | None = Query(default=None, max_length=64),
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: str | None = Query(default=None, max_length=64),
    actor_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.id.in_(select(stmt.subquery().c.id)))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[AuditLogOut.model_validate(log).model_copy(update={"actor_name": name}) for log, name in rows],
        total=total,
    )
