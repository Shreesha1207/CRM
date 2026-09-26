"""Admin booking management, conflict resolution and the calendar feed."""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DB
from app.api.deps import require as require_permission
from app.api.routes.bookings import create_with_auto_assign
from app.api.serializers import booking_out, bookings_out, resource_out
from app.core.errors import AppError, NotFound
from app.core.permissions import Permission
from app.core.timeutils import day_bounds, get_zone, utcnow
from app.jobs.runner import dispatch_notifications
from app.models import AuditLog, AvailabilityException, Booking, BookingResource, Resource, User
from app.models.enums import BookingStatus, RecordStatus
from app.schemas import (
    AdminBookingCreateIn,
    AdminBookingUpdateIn,
    AdminRescheduleIn,
    AlternativeOut,
    AuditLogOut,
    BookingOut,
    CancelIn,
    ConflictResolveIn,
    ExceptionOut,
    Page,
    ReassignIn,
)
from app.services import audit, settings_service
from app.services.availability import resource_timezone
from app.services.booking_engine import BookingEngine, suggest_alternatives

router = APIRouter(prefix="/api/admin", tags=["admin: bookings"])
ViewAll = Depends(require_permission(Permission.BOOKINGS_VIEW_ALL))
Manage = Depends(require_permission(Permission.BOOKINGS_MANAGE))
ManageConflicts = Depends(require_permission(Permission.CONFLICTS_MANAGE))


def _range(db, start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    tz = get_zone(settings_service.get_rules(db).default_timezone)
    lo = day_bounds(start, tz)[0] if start else None
    hi = day_bounds(end, tz)[1] if end else None
    return lo, hi


@router.get("/bookings", response_model=Page[BookingOut])
def list_bookings(
    db: DB,
    actor: User = ViewAll,
    status_filter: list[BookingStatus] = Query(default=[], alias="status"),
    user_id: uuid.UUID | None = None,
    resource_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[BookingOut]:
    stmt = select(Booking)
    if status_filter:
        stmt = stmt.where(Booking.status.in_(status_filter))
    if user_id:
        stmt = stmt.where(Booking.user_id == user_id)
    if resource_id:
        stmt = stmt.where(
            or_(
                Booking.primary_resource_id == resource_id,
                Booking.id.in_(select(BookingResource.booking_id).where(BookingResource.resource_id == resource_id)),
            )
        )
    if service_id:
        stmt = stmt.where(Booking.service_id == service_id)
    if location_id:
        stmt = stmt.where(Booking.location_id == location_id)
    lo, hi = _range(db, start_date, end_date)
    if lo:
        stmt = stmt.where(Booking.end_datetime > lo)
    if hi:
        stmt = stmt.where(Booking.start_datetime < hi)
    if q:
        stmt = stmt.where(Booking.user_id.in_(select(User.id).where(or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Booking.start_datetime.desc()).limit(limit).offset(offset)).unique().all()
    rules = settings_service.get_rules(db)
    return Page(items=bookings_out(db, rows, actor, rules, utcnow()), total=total)


@router.post("/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def admin_create_booking(body: AdminBookingCreateIn, db: DB, background: BackgroundTasks, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = create_with_auto_assign(engine, body, user_id=body.user_id, override_rules=body.override_rules)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, actor, engine.rules, engine.now)


@router.get("/bookings/{booking_id}", response_model=BookingOut)
def admin_get_booking(booking_id: uuid.UUID, db: DB, actor: User = ViewAll) -> BookingOut:
    engine = BookingEngine(db, actor)
    return booking_out(db, engine.get_visible_booking(booking_id), actor, engine.rules, engine.now)


@router.get("/bookings/{booking_id}/history", response_model=list[AuditLogOut])
def booking_history(booking_id: uuid.UUID, db: DB, actor: User = ViewAll) -> list[AuditLogOut]:
    """Audit trail across the whole reschedule chain of this booking."""
    chain: list[uuid.UUID] = []
    current = db.get(Booking, booking_id)
    if current is None:
        raise NotFound("Booking")
    while current is not None and current.id not in chain:
        chain.append(current.id)
        current = db.get(Booking, current.rescheduled_from_id) if current.rescheduled_from_id else None
    successor = booking_id
    while True:
        nxt = db.scalar(select(Booking.id).where(Booking.rescheduled_from_id == successor))
        if nxt is None or nxt in chain:
            break
        chain.append(nxt)
        successor = nxt
    rows = db.execute(
        select(AuditLog, User.name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.entity_type == "booking", AuditLog.entity_id.in_([str(i) for i in chain]))
        .order_by(AuditLog.created_at)
    ).all()
    return [AuditLogOut.model_validate(log).model_copy(update={"actor_name": name}) for log, name in rows]


@router.put("/bookings/{booking_id}", response_model=BookingOut)
def admin_update_booking(booking_id: uuid.UUID, body: AdminBookingUpdateIn, db: DB, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = engine.get_visible_booking(booking_id)
    changes = body.model_dump(exclude_unset=True)
    old = audit.snapshot(booking, *changes.keys())
    for key, value in changes.items():
        setattr(booking, key, value)
    audit.record(db, actor_id=actor.id, action="BOOKING_UPDATED", entity_type="booking", entity_id=booking.id, old=old, new=changes)
    db.commit()
    return booking_out(db, booking, actor, engine.rules, engine.now)


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def admin_cancel(booking_id: uuid.UUID, body: CancelIn, db: DB, background: BackgroundTasks, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = engine.cancel(booking_id, reason=body.reason)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, actor, engine.rules, engine.now)


@router.post("/bookings/{booking_id}/reschedule", response_model=BookingOut)
def admin_reschedule(booking_id: uuid.UUID, body: AdminRescheduleIn, db: DB, background: BackgroundTasks, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = engine.reschedule(
        booking_id, new_start=body.start, new_resource_id=body.resource_id,
        override_rules=body.override_rules, reason=body.reason,
    )
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, actor, engine.rules, engine.now)


@router.post("/bookings/{booking_id}/reassign", response_model=BookingOut)
def admin_reassign(booking_id: uuid.UUID, body: ReassignIn, db: DB, background: BackgroundTasks, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = engine.reassign(booking_id, body.resource_id, override_rules=body.override_rules, reason=body.reason)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, actor, engine.rules, engine.now)


@router.post("/bookings/{booking_id}/confirm", response_model=BookingOut)
def admin_confirm(booking_id: uuid.UUID, db: DB, background: BackgroundTasks, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = engine.confirm(booking_id)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, actor, engine.rules, engine.now)


@router.post("/bookings/{booking_id}/complete", response_model=BookingOut)
def admin_complete(booking_id: uuid.UUID, db: DB, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    return booking_out(db, engine.complete(booking_id), actor, engine.rules, engine.now)


@router.post("/bookings/{booking_id}/no-show", response_model=BookingOut)
def admin_no_show(booking_id: uuid.UUID, db: DB, actor: User = Manage) -> BookingOut:
    engine = BookingEngine(db, actor)
    return booking_out(db, engine.mark_no_show(booking_id), actor, engine.rules, engine.now)


# ---------------------------------------------------------------- conflicts


@router.get("/conflicts", response_model=Page[BookingOut])
def list_conflicts(
    db: DB, actor: User = ManageConflicts, limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)
) -> Page[BookingOut]:
    stmt = select(Booking).where(Booking.status == BookingStatus.CONFLICTED)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Booking.start_datetime).limit(limit).offset(offset)).unique().all()
    return Page(items=bookings_out(db, rows, actor, settings_service.get_rules(db), utcnow()), total=total)


@router.get("/conflicts/{booking_id}")
def get_conflict(booking_id: uuid.UUID, db: DB, actor: User = ManageConflicts) -> dict:
    engine = BookingEngine(db, actor)
    booking = engine.get_visible_booking(booking_id)
    if booking.status != BookingStatus.CONFLICTED:
        raise NotFound("Conflict")
    options = suggest_alternatives(
        db, service_id=booking.service_id, resource_id=booking.primary_resource_id,
        start=booking.start_datetime, quantity=booking.quantity,
        keep_length=booking.end_datetime - booking.start_datetime,
    )
    return {
        "booking": booking_out(db, booking, actor, engine.rules, engine.now),
        "alternatives": [
            AlternativeOut(
                resource_id=r.id, resource_name=r.name, timezone=resource_timezone(r, engine.rules).key,
                start=slot.start.astimezone(resource_timezone(r, engine.rules)),
                end=slot.end.astimezone(resource_timezone(r, engine.rules)),
                same_resource=r.id == booking.primary_resource_id, remaining=slot.remaining,
            )
            for r, slot in options
        ],
    }


@router.post("/conflicts/{booking_id}/resolve", response_model=BookingOut)
def resolve_conflict(
    booking_id: uuid.UUID, body: ConflictResolveIn, db: DB, background: BackgroundTasks, actor: User = ManageConflicts
) -> BookingOut:
    engine = BookingEngine(db, actor)
    booking = engine.get_visible_booking(booking_id)
    if booking.status != BookingStatus.CONFLICTED:
        raise NotFound("Conflict")
    if body.action == "override":
        result = engine.resolve_override(booking_id, note=body.reason)
    elif body.action == "cancel":
        result = engine.cancel(booking_id, reason=body.reason or "Schedule conflict")
    elif body.action == "reschedule":
        result = engine.reschedule(
            booking_id, new_start=body.start, new_resource_id=body.resource_id,
            override_rules=body.override_rules, reason=body.reason,
        )
    else:
        result = engine.reassign(booking_id, body.resource_id, override_rules=body.override_rules, reason=body.reason)
    if body.action != "override":
        audit.record(
            db, actor_id=actor.id, action="CONFLICT_RESOLVED", entity_type="booking", entity_id=booking_id,
            new={"resolution": body.action, "result_booking_id": result.id},
        )
        db.commit()
    background.add_task(dispatch_notifications)
    return booking_out(db, result, actor, engine.rules, engine.now)


# ---------------------------------------------------------------- calendar


@router.get("/calendar")
def calendar(
    db: DB,
    start: date,
    end: date,
    actor: User = ViewAll,
    resource_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    status_filter: list[BookingStatus] = Query(default=[], alias="status"),
) -> dict:
    """Bookings and blocked periods between two dates (inclusive), for the day/week/month views."""
    if end < start or (end - start) > timedelta(days=62):
        raise AppError("INVALID_RANGE", "Choose a range of at most 62 days", status_code=422)
    rules = settings_service.get_rules(db)
    lo, hi = _range(db, start, end)

    resources_stmt = select(Resource).where(Resource.status == RecordStatus.ACTIVE)
    if resource_id:
        resources_stmt = resources_stmt.where(Resource.id == resource_id)
    if location_id:
        resources_stmt = resources_stmt.where(Resource.location_id == location_id)
    if service_id:
        resources_stmt = resources_stmt.where(Resource.service_links.any(service_id=service_id))
    resources = db.scalars(resources_stmt.order_by(Resource.name)).unique().all()

    stmt = select(Booking).where(Booking.start_datetime < hi, Booking.end_datetime > lo)
    stmt = stmt.where(Booking.status.in_(status_filter) if status_filter else Booking.status.notin_([BookingStatus.RESCHEDULED]))
    if resource_id:
        stmt = stmt.where(Booking.primary_resource_id == resource_id)
    if service_id:
        stmt = stmt.where(Booking.service_id == service_id)
    if location_id:
        stmt = stmt.where(Booking.location_id == location_id)
    bookings = db.scalars(stmt.order_by(Booking.start_datetime)).unique().all()

    resource_ids = [r.id for r in resources]
    location_ids = {r.location_id for r in resources if r.location_id}
    blocks_stmt = select(AvailabilityException).where(
        AvailabilityException.start_datetime < hi,
        AvailabilityException.end_datetime > lo,
        or_(
            AvailabilityException.resource_id.in_(resource_ids),
            (AvailabilityException.resource_id.is_(None) & AvailabilityException.location_id.in_(location_ids)),
            (AvailabilityException.resource_id.is_(None) & AvailabilityException.location_id.is_(None)),
        ),
    )
    blocks = db.scalars(blocks_stmt.order_by(AvailabilityException.start_datetime)).all()
    return {
        "start": lo,
        "end": hi,
        "timezone": rules.default_timezone,
        "resources": [resource_out(r, rules) for r in resources],
        "bookings": bookings_out(db, bookings, actor, rules, utcnow()),
        "blocks": [ExceptionOut.model_validate(b) for b in blocks],
    }
