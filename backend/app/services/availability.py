"""Availability engine.

Answers one question for every industry:

    "Is RESOURCE X available for SERVICE Y between START and END?"

    AVAILABLE TIME = Operating Hours ∩ Resource Availability
                     − Blocked periods / exceptions
                     − Existing bookings (with buffers)

Slot listing and booking validation both go through `ResourceSnapshot.check`,
so what the UI shows and what the booking engine accepts can never disagree.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BookingRejected
from app.core.timeutils import day_bounds, get_zone
from app.models import (
    AvailabilityException,
    Booking,
    BookingResource,
    OperatingHours,
    Resource,
    ResourceAvailability,
    ResourceService,
    Service,
)
from app.models.enums import (
    BLOCKING_EXCEPTION_TYPES,
    BookingType,
    ExceptionType,
    RecordStatus,
)
from app.services.intervals import Interval, contains, intersect, overlaps, subtract
from app.services.schedule import WeeklyRule, build_schedule, schedule_days_for_range
from app.services.settings_service import BookingRules

# Reason codes shared by slot listings and booking rejections.
AVAILABLE = "AVAILABLE"
PAST = "PAST"
TOO_SOON = "TOO_SOON"
TOO_FAR_AHEAD = "TOO_FAR_AHEAD"
OUTSIDE_OPERATING_HOURS = "OUTSIDE_OPERATING_HOURS"
OUTSIDE_AVAILABILITY = "OUTSIDE_AVAILABILITY"
BLOCKED = "BLOCKED"
CONFLICT = "CONFLICT"
FULL = "CAPACITY_REACHED"
QUANTITY_TOO_LARGE = "QUANTITY_EXCEEDS_CAPACITY"

REASON_MESSAGES = {
    PAST: "This time is in the past",
    TOO_SOON: "This time is inside the minimum booking notice",
    TOO_FAR_AHEAD: "This time is beyond the advance booking window",
    OUTSIDE_OPERATING_HOURS: "This time is outside operating hours",
    OUTSIDE_AVAILABILITY: "The resource is not available at this time",
    BLOCKED: "This time is blocked",
    CONFLICT: "This time overlaps another booking",
    FULL: "This session is full",
    QUANTITY_TOO_LARGE: "The requested quantity exceeds the capacity",
}


def resource_timezone(resource: Resource, rules: BookingRules) -> ZoneInfo:
    tz_name = resource.location.timezone if resource.location else None
    return get_zone(tz_name, rules.default_timezone)


# --------------------------------------------------------------------------
# Offering: the effective terms of one service on one resource
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Offering:
    service: Service
    resource: Resource
    duration: timedelta
    buffer_before: timedelta
    buffer_after: timedelta
    price: Decimal | None
    booking_type: BookingType
    capacity: int | None  # seats per session (CAPACITY) / max quantity (INDIVIDUAL)

    @property
    def exclusive(self) -> bool:
        return self.booking_type == BookingType.INDIVIDUAL

    def session_key(self, start: datetime) -> str:
        """Allocation key shared by all seats of one capacity session."""
        end = start + self.duration
        return f"s:{self.service.id}:{start.timestamp():.0f}:{end.timestamp():.0f}"

    def allocation_key(self, booking_id: uuid.UUID, start: datetime) -> str:
        return f"x:{booking_id}" if self.exclusive else self.session_key(start)


def resolve_offering(
    db: Session, service: Service, resource: Resource, rules: BookingRules
) -> Offering:
    link = db.get(ResourceService, (resource.id, service.id))
    if link is None or link.status != RecordStatus.ACTIVE:
        raise BookingRejected(
            "SERVICE_NOT_OFFERED",
            f"{resource.name} does not provide {service.name}",
            status_code=422,
        )

    def pick(*values: int | None) -> int:
        return next(v for v in values if v is not None)

    duration = pick(link.custom_duration, service.duration_minutes)
    before = pick(link.custom_buffer_before, service.buffer_before, rules.default_buffer_before)
    after = pick(link.custom_buffer_after, service.buffer_after, rules.default_buffer_after)

    if service.booking_type == BookingType.CAPACITY:
        limits = [c for c in (service.capacity, resource.capacity) if c]
        capacity: int | None = min(limits) if limits else 1
    else:
        capacity = resource.capacity

    return Offering(
        service=service,
        resource=resource,
        duration=timedelta(minutes=duration),
        buffer_before=timedelta(minutes=before),
        buffer_after=timedelta(minutes=after),
        price=link.custom_price if link.custom_price is not None else service.price,
        booking_type=service.booking_type,
        capacity=capacity,
    )


# --------------------------------------------------------------------------
# Snapshot of everything that constrains one resource over a time range
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    start: datetime
    end: datetime
    type: ExceptionType
    reason: str | None


@dataclass(frozen=True)
class Occupancy:
    booking_id: uuid.UUID
    start: datetime  # occupied range, buffers included
    end: datetime
    allocation_key: str
    quantity: int


@dataclass(frozen=True)
class SlotCheck:
    start: datetime
    end: datetime
    code: str
    remaining: int | None = None
    capacity: int | None = None
    detail: str | None = None

    @property
    def available(self) -> bool:
        return self.code == AVAILABLE

    @property
    def message(self) -> str:
        return self.detail or REASON_MESSAGES.get(self.code, "Available")


@dataclass
class ResourceSnapshot:
    resource: Resource
    tz: ZoneInfo
    window: Interval
    operating: list[Interval]
    schedule: list[Interval]
    blocks: list[Block]
    occupancy: list[Occupancy] = field(default_factory=list)

    @property
    def base(self) -> list[Interval]:
        """Bookable time before blocks and bookings are taken out."""
        return intersect(self.operating, self.schedule)

    @property
    def free(self) -> list[Interval]:
        return subtract(self.base, [(b.start, b.end) for b in self.blocks])

    def schedule_violation(self, start: datetime, end: datetime) -> tuple[str, str | None] | None:
        """Why [start, end) is not bookable time on this resource, if it isn't.

        Blocked periods override regular availability, so a block wins even
        inside otherwise available hours.
        """
        if not contains(self.operating, start, end):
            return OUTSIDE_OPERATING_HOURS, None
        if not contains(self.schedule, start, end):
            return OUTSIDE_AVAILABILITY, None
        for block in self.blocks:
            if overlaps(start, end, block.start, block.end):
                label = block.type.value.replace("_", " ").capitalize()
                return BLOCKED, f"{label}: {block.reason}" if block.reason else f"{label} period"
        return None

    def check(
        self,
        offering: Offering,
        start: datetime,
        *,
        quantity: int,
        rules: BookingRules,
        now: datetime,
        override_rules: bool = False,
        ignore_notice: bool = False,
    ) -> SlotCheck:
        """Validate one candidate booking against this snapshot.

        ``override_rules`` (admin only) skips notice, window, hours,
        availability and block checks. It never skips the clash and capacity
        checks: an exclusive resource can never be double booked and capacity
        can never go negative.
        """
        end = start + offering.duration

        def result(code: str, **kw: object) -> SlotCheck:
            return SlotCheck(start=start, end=end, code=code, **kw)  # type: ignore[arg-type]

        if not override_rules:
            if not ignore_notice:
                if start < now:
                    return result(PAST)
                if start < now + timedelta(minutes=rules.minimum_booking_notice):
                    return result(TOO_SOON)
                if start > now + timedelta(days=rules.maximum_advance_booking_days):
                    return result(TOO_FAR_AHEAD)
            violation = self.schedule_violation(start, end)
            if violation:
                return result(violation[0], detail=violation[1])

        occupied_start = start - offering.buffer_before
        occupied_end = end + offering.buffer_after
        session_key = None if offering.exclusive else offering.session_key(start)
        used = 0
        for occ in self.occupancy:
            if not overlaps(occupied_start, occupied_end, occ.start, occ.end):
                continue
            if session_key is not None and occ.allocation_key == session_key:
                used += occ.quantity
            else:
                return result(CONFLICT)

        if offering.exclusive:
            if offering.capacity is not None and quantity > offering.capacity:
                return result(QUANTITY_TOO_LARGE, capacity=offering.capacity)
            return result(AVAILABLE)

        capacity = offering.capacity or 1
        remaining = max(capacity - used, 0)
        if quantity > capacity:
            return result(QUANTITY_TOO_LARGE, remaining=remaining, capacity=capacity)
        if quantity > remaining:
            return result(FULL, remaining=remaining, capacity=capacity)
        return result(AVAILABLE, remaining=remaining, capacity=capacity)


def _weekly_rules(rows: Iterable[OperatingHours | ResourceAvailability]) -> list[WeeklyRule]:
    return [
        WeeklyRule(
            day_of_week=r.day_of_week,
            start_time=r.start_time,
            end_time=r.end_time,
            valid_from=getattr(r, "valid_from", None),
            valid_until=getattr(r, "valid_until", None),
            is_available=getattr(r, "is_available", True),
        )
        for r in rows
    ]


def _operating_rules(db: Session, location_id: uuid.UUID | None) -> list[WeeklyRule]:
    if location_id is not None:
        own = db.scalars(select(OperatingHours).where(OperatingHours.location_id == location_id)).all()
        if own:
            return _weekly_rules(own)
    return _weekly_rules(
        db.scalars(select(OperatingHours).where(OperatingHours.location_id.is_(None))).all()
    )


def _exceptions_for(
    db: Session, resource: Resource, window: Interval
) -> list[AvailabilityException]:
    scope = [AvailabilityException.resource_id == resource.id]
    scope.append(
        and_(
            AvailabilityException.resource_id.is_(None),
            AvailabilityException.location_id.is_(None),
        )
    )
    if resource.location_id is not None:
        scope.append(
            and_(
                AvailabilityException.resource_id.is_(None),
                AvailabilityException.location_id == resource.location_id,
            )
        )
    return list(
        db.scalars(
            select(AvailabilityException).where(
                or_(*scope),
                AvailabilityException.start_datetime < window[1],
                AvailabilityException.end_datetime > window[0],
            )
        )
    )


def load_occupancy(
    db: Session,
    resource_id: uuid.UUID,
    window: Interval,
    exclude_booking_ids: Iterable[uuid.UUID] = (),
) -> list[Occupancy]:
    stmt = (
        select(BookingResource, Booking.quantity)
        .join(Booking, Booking.id == BookingResource.booking_id)
        .where(
            BookingResource.resource_id == resource_id,
            BookingResource.active.is_(True),
            BookingResource.occupied_start < window[1],
            BookingResource.occupied_end > window[0],
        )
    )
    excluded = list(exclude_booking_ids)
    if excluded:
        stmt = stmt.where(BookingResource.booking_id.not_in(excluded))
    return [
        Occupancy(
            booking_id=alloc.booking_id,
            start=alloc.occupied_start,
            end=alloc.occupied_end,
            allocation_key=alloc.allocation_key,
            quantity=quantity,
        )
        for alloc, quantity in db.execute(stmt)
    ]


def build_snapshot(
    db: Session,
    resource: Resource,
    window_start: datetime,
    window_end: datetime,
    rules: BookingRules,
    *,
    exclude_booking_ids: Iterable[uuid.UUID] = (),
    include_occupancy: bool = True,
) -> ResourceSnapshot:
    tz = resource_timezone(resource, rules)
    days = schedule_days_for_range(window_start, window_end, tz)
    # Look one day beyond each side so overnight periods and buffers are seen.
    window = (window_start - timedelta(days=1), window_end + timedelta(days=1))

    exceptions = _exceptions_for(db, resource, window)
    blocks = [
        Block(e.start_datetime, e.end_datetime, e.type, e.reason)
        for e in exceptions
        if e.type in BLOCKING_EXCEPTION_TYPES
    ]
    special = [e for e in exceptions if e.type == ExceptionType.SPECIAL_HOURS]
    # Special hours on the resource replace its own schedule; on a location
    # (or globally) they replace the operating hours.
    resource_special = [(e.start_datetime, e.end_datetime) for e in special if e.resource_id]
    operating_special = [(e.start_datetime, e.end_datetime) for e in special if not e.resource_id]

    if rules.enforce_operating_hours:
        operating = build_schedule(
            _operating_rules(db, resource.location_id), operating_special, days, tz
        )
    else:
        operating = build_schedule([], [], days, tz)

    schedule = build_schedule(
        _weekly_rules(resource.availability_rules), resource_special, days, tz
    )

    occupancy = (
        load_occupancy(db, resource.id, window, exclude_booking_ids) if include_occupancy else []
    )
    return ResourceSnapshot(
        resource=resource,
        tz=tz,
        window=(window_start, window_end),
        operating=operating,
        schedule=schedule,
        blocks=blocks,
        occupancy=occupancy,
    )


# --------------------------------------------------------------------------
# Slot generation
# --------------------------------------------------------------------------


def candidate_starts(
    base: Sequence[Interval],
    duration: timedelta,
    step: timedelta,
    day_start: datetime,
    day_end: datetime,
) -> list[datetime]:
    """Slot starts on a fixed grid anchored at the start of each base window.

    Blocks and bookings are applied afterwards, so they mark slots unavailable
    instead of shifting the grid. Only slots that can hold the full service
    duration inside the window are produced. Steps are absolute time, so the
    grid stays correct across daylight-saving transitions.
    """
    starts: list[datetime] = []
    for window_start, window_end in base:
        t = window_start
        while t + duration <= window_end:
            if day_start <= t < day_end:
                starts.append(t)
            t += step
    return sorted(set(starts))


def generate_slots(
    db: Session,
    offering: Offering,
    day: date,
    rules: BookingRules,
    now: datetime,
    *,
    quantity: int = 1,
) -> tuple[ZoneInfo, list[SlotCheck]]:
    resource = offering.resource
    tz = resource_timezone(resource, rules)
    day_start, day_end = day_bounds(day, tz)
    snapshot = build_snapshot(db, resource, day_start, day_end + offering.duration, rules)
    step = timedelta(minutes=rules.slot_interval) if rules.slot_interval else offering.duration

    if resource.status != RecordStatus.ACTIVE or offering.service.status != RecordStatus.ACTIVE:
        return tz, []

    starts = candidate_starts(snapshot.base, offering.duration, step, day_start, day_end)
    return tz, [
        snapshot.check(offering, s, quantity=quantity, rules=rules, now=now) for s in starts
    ]
