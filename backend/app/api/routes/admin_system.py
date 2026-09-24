"""Admin: users, settings, dashboard, reports and the audit log."""

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update

from app.api.deps import DB
from app.api.deps import require as require_permission
from app.core.errors import AppError
from app.core.permissions import Permission, can_assign_role
from app.core.security import hash_password
from app.core.timeutils import day_bounds, get_zone, utcnow
from app.models import AuditLog, AuthSession, User
from app.models.enums import Role, UserStatus
from app.schemas import AuditLogOut, Page, SettingsIn, SettingsOut, UserCreateIn, UserOut, UserUpdateIn
from app.services import audit, reports, settings_service

router = APIRouter(prefix="/api/admin", tags=["admin: system"])


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
    if not can_assign_role(actor.role, body.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"You cannot create {body.role.value} accounts")
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this e-mail already exists")
    user = User(
        name=body.name.strip(), email=email, phone=body.phone,
        password_hash=hash_password(body.password), role=body.role, status=body.status,
    )
    db.add(user)
    db.flush()
    audit.record(db, actor_id=actor.id, action="USER_CREATED", entity_type="user", entity_id=user.id, new={"email": email, "role": body.role, "status": body.status})
    db.commit()
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID, body: UserUpdateIn, db: DB, actor: User = Depends(require_permission(Permission.USERS_MANAGE))
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = body.model_dump(exclude_unset=True)
    if "role" in changes and changes["role"] != user.role:
        if user.id == actor.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role")
        if not can_assign_role(actor.role, changes["role"]) or not can_assign_role(actor.role, user.role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot assign that role")
    if "status" in changes and changes["status"] != UserStatus.ACTIVE and user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")

    old = audit.snapshot(user, "name", "phone", "role", "status")
    password = changes.pop("password", None)
    for key, value in changes.items():
        setattr(user, key, value)
    revoke = password is not None or changes.get("status", UserStatus.ACTIVE) != UserStatus.ACTIVE
    if password is not None:
        user.password_hash = hash_password(password)
    if revoke:
        db.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.record(
        db, actor_id=actor.id, action="USER_UPDATED", entity_type="user", entity_id=user.id,
        old=old, new={**changes, "password_changed": password is not None},
    )
    db.commit()
    return user


# ---------------------------------------------------------------- settings


@router.get("/settings", response_model=SettingsOut)
def get_settings_values(db: DB, _: User = Depends(require_permission(Permission.SETTINGS_MANAGE))) -> SettingsOut:
    return SettingsOut(values=settings_service.get_all(db), definitions=settings_service.describe())


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: SettingsIn, db: DB, actor: User = Depends(require_permission(Permission.SETTINGS_MANAGE))) -> SettingsOut:
    old = settings_service.get_all(db)
    try:
        values = settings_service.update(db, body.values, actor.id)
    except settings_service.SettingsValidationError as exc:
        raise AppError("INVALID_SETTING", str(exc), status_code=422) from exc
    audit.record(
        db, actor_id=actor.id, action="SETTINGS_CHANGED", entity_type="settings",
        old={k: old[k] for k in body.values}, new=body.values,
    )
    db.commit()
    return SettingsOut(values=values, definitions=settings_service.describe())


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
