"""Bookings for the signed-in user. Every query is scoped to the caller."""

import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DB, CurrentUser
from app.api.serializers import booking_out, bookings_out
from app.core.errors import BookingRejected, NotFound
from app.core.timeutils import utcnow
from app.jobs.runner import dispatch_notifications
from app.models import Booking, Resource
from app.models.enums import BookingStatus, RecordStatus
from app.schemas import (
    BookingCreateIn,
    BookingOut,
    CancelIn,
    OccurrenceOut,
    Page,
    RecurringCreatedOut,
    RecurringIn,
    RecurringPreviewOut,
    RescheduleIn,
)
from app.services.booking_engine import BookingEngine, BookingRequest, RecurrenceSpec

router = APIRouter(prefix="/api/bookings", tags=["bookings"])
S = BookingStatus

Scope = Literal["upcoming", "past", "cancelled", "waitlisted", "all"]


def create_with_auto_assign(engine: BookingEngine, body: BookingCreateIn, **fields) -> Booking:
    """Book the requested resource, or, when none was given, the first
    suitable resource that is free (Service -> Date -> Slot flow)."""

    def request(resource_id: uuid.UUID) -> BookingRequest:
        return BookingRequest(
            service_id=body.service_id,
            resource_id=resource_id,
            start=body.start,
            quantity=body.quantity,
            notes=body.notes,
            additional_resource_ids=body.additional_resource_ids,
            join_waitlist=body.join_waitlist,
            duration_minutes=body.duration_minutes,
            **fields,
        )

    if body.resource_id is not None:
        return engine.create(request(body.resource_id))
    candidates = engine.db.scalars(
        select(Resource.id)
        .where(
            Resource.status == RecordStatus.ACTIVE,
            Resource.service_links.any(service_id=body.service_id, status=RecordStatus.ACTIVE),
        )
        .order_by(Resource.name)
    ).all()
    if not candidates:
        raise NotFound("Resource")
    reasons = []
    for rid in candidates:
        try:
            return engine.create(request(rid))
        except BookingRejected as exc:
            engine.db.rollback()
            reasons.append(exc.code)
    raise BookingRejected(
        "NO_RESOURCE_AVAILABLE", "No resource is available at that time", details={"reasons": reasons}
    )


def scope_filter(stmt, scope: Scope, now):
    if scope == "upcoming":
        return stmt.where(Booking.status.in_([S.PENDING, S.CONFIRMED, S.CONFLICTED]), Booking.end_datetime > now).order_by(Booking.start_datetime)
    if scope == "past":
        return stmt.where(
            or_(
                Booking.status.in_([S.COMPLETED, S.NO_SHOW]),
                (Booking.status.in_([S.PENDING, S.CONFIRMED, S.CONFLICTED])) & (Booking.end_datetime <= now),
            )
        ).order_by(Booking.start_datetime.desc())
    if scope == "cancelled":
        return stmt.where(Booking.status == S.CANCELLED).order_by(Booking.start_datetime.desc())
    if scope == "waitlisted":
        return stmt.where(Booking.status == S.WAITLISTED).order_by(Booking.start_datetime)
    return stmt.order_by(Booking.start_datetime.desc())


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(body: BookingCreateIn, user: CurrentUser, db: DB, background: BackgroundTasks) -> BookingOut:
    engine = BookingEngine(db, user)
    booking = create_with_auto_assign(engine, body, user_id=user.id)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, user, engine.rules, engine.now)


@router.get("", response_model=Page[BookingOut])
def my_bookings(
    user: CurrentUser,
    db: DB,
    scope: Scope = "all",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[BookingOut]:
    engine = BookingEngine(db, user)
    base = select(Booking).where(Booking.user_id == user.id)
    stmt = scope_filter(base, scope, engine.now)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.limit(limit).offset(offset)).unique().all()
    return Page(items=bookings_out(db, rows, user, engine.rules, engine.now), total=total)


@router.post("/recurring/preview", response_model=RecurringPreviewOut)
def preview_recurring(body: RecurringIn, user: CurrentUser, db: DB) -> RecurringPreviewOut:
    engine = BookingEngine(db, user)
    request, spec = _recurring_request(body, user.id)
    results = engine.preview_recurring(request, spec)
    occurrences = [OccurrenceOut(start=r.start, end=r.end, available=r.available, code=r.code, message=r.message) for r in results]
    available = sum(1 for o in occurrences if o.available)
    return RecurringPreviewOut(occurrences=occurrences, available_count=available, conflict_count=len(occurrences) - available)


@router.post("/recurring", response_model=RecurringCreatedOut, status_code=status.HTTP_201_CREATED)
def create_recurring(body: RecurringIn, user: CurrentUser, db: DB, background: BackgroundTasks) -> RecurringCreatedOut:
    engine = BookingEngine(db, user)
    request, spec = _recurring_request(body, user.id)
    series, created, skipped = engine.create_recurring(request, spec, skip_conflicts=body.skip_conflicts)
    background.add_task(dispatch_notifications)
    return RecurringCreatedOut(
        series_id=series.id,
        created=bookings_out(db, created, user, engine.rules, engine.now),
        skipped=[OccurrenceOut(start=r.start, end=r.end, available=False, code=r.code, message=r.message) for r in skipped],
    )


def _recurring_request(body: RecurringIn, default_user_id: uuid.UUID) -> tuple[BookingRequest, RecurrenceSpec]:
    if body.resource_id is None:
        raise BookingRejected("RESOURCE_REQUIRED", "Choose a resource for recurring bookings", status_code=422)
    request = BookingRequest(
        user_id=body.user_id or default_user_id,
        service_id=body.service_id,
        resource_id=body.resource_id,
        start=body.start,
        quantity=body.quantity,
        notes=body.notes,
        override_rules=body.override_rules,
        duration_minutes=body.duration_minutes,
    )
    spec = RecurrenceSpec(
        frequency=body.frequency, interval=body.interval, count=body.count, custom_starts=body.custom_starts
    )
    return request, spec


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(booking_id: uuid.UUID, user: CurrentUser, db: DB) -> BookingOut:
    engine = BookingEngine(db, user)
    booking = engine.get_visible_booking(booking_id)
    return booking_out(db, booking, user, engine.rules, engine.now)


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: uuid.UUID, body: CancelIn, user: CurrentUser, db: DB, background: BackgroundTasks) -> BookingOut:
    engine = BookingEngine(db, user)
    booking = engine.cancel(booking_id, reason=body.reason)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, user, engine.rules, utcnow())


@router.post("/{booking_id}/reschedule", response_model=BookingOut)
def reschedule_booking(booking_id: uuid.UUID, body: RescheduleIn, user: CurrentUser, db: DB, background: BackgroundTasks) -> BookingOut:
    engine = BookingEngine(db, user)
    booking = engine.reschedule(booking_id, new_start=body.start, new_resource_id=body.resource_id)
    background.add_task(dispatch_notifications)
    return booking_out(db, booking, user, engine.rules, utcnow())
