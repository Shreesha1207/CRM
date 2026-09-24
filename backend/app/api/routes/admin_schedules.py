"""Operating hours and date-specific exceptions (blocks, holidays, special hours)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select

from app.api.deps import DB
from app.api.deps import require as require_permission
from app.api.routes.admin_catalog import change_out
from app.core.errors import AppError, NotFound
from app.core.permissions import Permission
from app.core.timeutils import ensure_aware, get_zone
from app.models import AvailabilityException, Location, OperatingHours, Resource, User
from app.models.enums import ExceptionType
from app.schemas import (
    ConflictAction,
    ExceptionIn,
    ExceptionOut,
    OperatingHoursIn,
    OperatingHoursOut,
    ScheduleChangeOut,
)
from app.services import settings_service
from app.services.availability import resource_timezone
from app.services.booking_engine import apply_schedule_change

router = APIRouter(prefix="/api/admin", tags=["admin: schedules"])
ManageSchedules = Depends(require_permission(Permission.SCHEDULES_MANAGE))


def _scope_resource_ids(db, *, resource_id: uuid.UUID | None, location_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    """Resources whose availability a change in this scope can affect (None = all)."""
    if resource_id is not None:
        return [resource_id]
    if location_id is not None:
        return list(db.scalars(select(Resource.id).where(Resource.location_id == location_id)))
    return None


@router.get("/operating-hours", response_model=list[OperatingHoursOut], dependencies=[ManageSchedules])
def get_operating_hours(db: DB, location_id: uuid.UUID | None = None) -> list[OperatingHours]:
    """location_id omitted = the default hours used by locations without their own."""
    stmt = select(OperatingHours)
    stmt = stmt.where(
        OperatingHours.location_id == location_id if location_id else OperatingHours.location_id.is_(None)
    )
    return list(db.scalars(stmt.order_by(OperatingHours.day_of_week, OperatingHours.start_time)))


@router.put("/operating-hours", response_model=ScheduleChangeOut)
def set_operating_hours(
    body: list[OperatingHoursIn],
    db: DB,
    location_id: uuid.UUID | None = None,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageSchedules,
) -> ScheduleChangeOut:
    """Replace the weekly operating hours. Days without an entry are closed;
    an empty list removes the restriction for this scope."""
    if location_id is not None and db.get(Location, location_id) is None:
        raise NotFound("Location")
    scope_filter = OperatingHours.location_id == location_id if location_id else OperatingHours.location_id.is_(None)
    old = [OperatingHoursOut.model_validate(r).model_dump(mode="json") for r in db.scalars(select(OperatingHours).where(scope_filter))]

    def mutate() -> None:
        db.execute(delete(OperatingHours).where(scope_filter))
        for item in body:
            db.add(OperatingHours(location_id=location_id, **item.model_dump()))

    change = apply_schedule_change(
        db, actor, mutate=mutate,
        resource_ids=_scope_resource_ids(db, resource_id=None, location_id=location_id),
        dry_run=dry_run, conflict_action=conflict_action, audit_action="OPERATING_HOURS_CHANGED",
        entity_type="location" if location_id else "settings", entity_id=location_id,
        audit_new={"old": old, "new": [i.model_dump(mode="json") for i in body]},
    )
    result = [
        OperatingHoursOut.model_validate(r).model_dump(mode="json")
        for r in db.scalars(select(OperatingHours).where(scope_filter).order_by(OperatingHours.day_of_week, OperatingHours.start_time))
    ]
    return change_out(change, result)


@router.get("/exceptions", response_model=list[ExceptionOut], dependencies=[ManageSchedules])
def list_exceptions(
    db: DB,
    resource_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    type: ExceptionType | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[AvailabilityException]:
    stmt = select(AvailabilityException)
    if resource_id:
        stmt = stmt.where(AvailabilityException.resource_id == resource_id)
    if location_id:
        stmt = stmt.where(AvailabilityException.location_id == location_id)
    if start:
        stmt = stmt.where(AvailabilityException.end_datetime > start)
    if end:
        stmt = stmt.where(AvailabilityException.start_datetime < end)
    if type:
        stmt = stmt.where(AvailabilityException.type == type)
    return list(db.scalars(stmt.order_by(AvailabilityException.start_datetime).limit(limit)))


@router.post("/exceptions", response_model=ScheduleChangeOut)
def create_exception(
    body: ExceptionIn,
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageSchedules,
) -> ScheduleChangeOut:
    """Block time, add a holiday / maintenance window or set special hours.

    Naive start/end are wall-clock times of the resource (or location).
    """
    rules = settings_service.get_rules(db)
    location_id = body.location_id
    if body.resource_id is not None:
        resource = db.get(Resource, body.resource_id)
        if resource is None:
            raise NotFound("Resource")
        tz = resource_timezone(resource, rules)
        location_id = None
    elif location_id is not None:
        location = db.get(Location, location_id)
        if location is None:
            raise NotFound("Location")
        tz = get_zone(location.timezone, rules.default_timezone)
    else:
        tz = get_zone(rules.default_timezone)
    start, end = ensure_aware(body.start, tz), ensure_aware(body.end, tz)
    if end <= start:
        raise AppError("INVALID_RANGE", "End must be after start", status_code=422)

    def mutate() -> AvailabilityException:
        exception = AvailabilityException(
            resource_id=body.resource_id, location_id=location_id, start_datetime=start,
            end_datetime=end, type=body.type, reason=body.reason, created_by=actor.id,
        )
        db.add(exception)
        return exception

    change = apply_schedule_change(
        db, actor, mutate=mutate,
        resource_ids=_scope_resource_ids(db, resource_id=body.resource_id, location_id=location_id),
        dry_run=dry_run, conflict_action=conflict_action,
        audit_action="BLOCK_CREATED" if body.type != ExceptionType.SPECIAL_HOURS else "SPECIAL_HOURS_CREATED",
        entity_type="availability_exception",
        audit_new={"resource_id": body.resource_id, "location_id": location_id, "start": start, "end": end, "type": body.type, "reason": body.reason},
    )
    result = ExceptionOut.model_validate(change.result).model_dump(mode="json") if change.applied else None
    return change_out(change, result)


@router.delete("/exceptions/{exception_id}", response_model=ScheduleChangeOut)
def delete_exception(
    exception_id: uuid.UUID,
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageSchedules,
) -> ScheduleChangeOut:
    exception = db.get(AvailabilityException, exception_id)
    if exception is None:
        raise NotFound("Exception")
    # Removing special hours can shrink availability, so this is a schedule change too.
    scope = _scope_resource_ids(db, resource_id=exception.resource_id, location_id=exception.location_id)
    old = ExceptionOut.model_validate(exception).model_dump(mode="json")

    change = apply_schedule_change(
        db, actor, mutate=lambda: db.delete(exception), resource_ids=scope, dry_run=dry_run,
        conflict_action=conflict_action, audit_action="BLOCK_DELETED", entity_type="availability_exception",
        entity_id=exception_id, audit_new={"old": old},
    )
    return change_out(change, {"deleted": change.applied})
