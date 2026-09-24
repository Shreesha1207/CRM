"""Booking engine: every state-changing booking operation goes through here.

Pattern used for each write:

    validate (fail fast) -> lock resource rows -> RE-validate -> write -> commit

Resource rows are locked with SELECT ... FOR UPDATE in a fixed order, so two
requests for the same resource are serialized and cannot deadlock. The second
validation runs after the lock, against committed data, which is what makes
"two users click Book at the same time" safe. The exclusion constraint on
booking_resources is a final database-level backstop.

Rescheduling strategy: a replacement booking is created and linked through
`rescheduled_from_id`; the original is marked RESCHEDULED. Both happen in one
transaction, so a failure leaves the original booking untouched.
"""

import uuid
from calendar import monthrange
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BookingRejected, Forbidden, NotFound
from app.core.permissions import Permission, has_permission
from app.core.timeutils import ensure_aware, localize, utcnow
from app.models import Booking, BookingResource, RecurringSeries, Resource, Service, User
from app.models.enums import (
    BookingStatus,
    BookingType,
    RecordStatus,
    RecurrenceFrequency,
    UserStatus,
)
from app.services import audit, events, settings_service
from app.services.availability import (
    AVAILABLE,
    FULL,
    REASON_MESSAGES,
    Offering,
    SlotCheck,
    build_snapshot,
    generate_slots,
    resolve_offering,
    resource_timezone,
)

S = BookingStatus

# Statuses a user or admin can still act on (cancel / reschedule).
OPEN_STATUSES = frozenset({S.PENDING, S.CONFIRMED, S.CONFLICTED})

ALLOWED_TRANSITIONS: dict[BookingStatus, frozenset[BookingStatus]] = {
    S.PENDING: frozenset({S.CONFIRMED, S.CANCELLED, S.RESCHEDULED, S.CONFLICTED}),
    S.CONFIRMED: frozenset({S.COMPLETED, S.NO_SHOW, S.CANCELLED, S.RESCHEDULED, S.CONFLICTED}),
    S.CONFLICTED: frozenset({S.CONFIRMED, S.CANCELLED, S.RESCHEDULED}),
    S.WAITLISTED: frozenset({S.CONFIRMED, S.PENDING, S.CANCELLED}),
    S.COMPLETED: frozenset(),
    S.CANCELLED: frozenset(),
    S.RESCHEDULED: frozenset(),
    S.NO_SHOW: frozenset(),
}


def transition(booking: Booking, new_status: BookingStatus) -> None:
    if new_status not in ALLOWED_TRANSITIONS[booking.status]:
        raise BookingRejected(
            "INVALID_STATUS_TRANSITION",
            f"A {booking.status.value.lower()} booking cannot become {new_status.value.lower()}",
            status_code=409,
        )
    booking.status = new_status


@dataclass
class BookingRequest:
    user_id: uuid.UUID
    service_id: uuid.UUID
    resource_id: uuid.UUID
    start: datetime  # naive = wall-clock time at the resource's location
    quantity: int = 1
    notes: str | None = None
    additional_resource_ids: Sequence[uuid.UUID] = ()
    join_waitlist: bool = False
    override_rules: bool = False


@dataclass
class RecurrenceSpec:
    frequency: RecurrenceFrequency | None = RecurrenceFrequency.WEEKLY
    interval: int = 1
    count: int = 1
    # "Custom" recurrence: explicit wall-clock starts instead of a rule.
    custom_starts: Sequence[datetime] = ()


@dataclass
class OccurrenceResult:
    start: datetime
    end: datetime
    code: str
    message: str

    @property
    def available(self) -> bool:
        return self.code == AVAILABLE


@dataclass
class AffectedBooking:
    booking_id: uuid.UUID
    code: str
    message: str
    summary: dict[str, Any] = field(default_factory=dict)


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def expand_recurrence(first_local: datetime, spec: RecurrenceSpec) -> list[datetime]:
    """Wall-clock occurrence starts. Stepping in local time keeps a weekly
    10:00 booking at 10:00 on both sides of a daylight-saving change."""
    if spec.custom_starts:
        return sorted({s.replace(tzinfo=None) for s in spec.custom_starts})
    out = []
    for i in range(spec.count):
        n = i * spec.interval
        if spec.frequency == RecurrenceFrequency.DAILY:
            out.append(first_local + timedelta(days=n))
        elif spec.frequency == RecurrenceFrequency.MONTHLY:
            out.append(add_months(first_local, n))
        else:
            out.append(first_local + timedelta(weeks=n))
    return out


class BookingEngine:
    def __init__(self, db: Session, actor: User, *, now: datetime | None = None) -> None:
        self.db = db
        self.actor = actor
        self.rules = settings_service.get_rules(db)
        self.now = now or utcnow()
        self.can_manage = has_permission(actor.role, Permission.BOOKINGS_MANAGE)
        self.can_override = has_permission(actor.role, Permission.OVERRIDE_RULES)

    # ------------------------------------------------------------------
    # Loading & locking helpers
    # ------------------------------------------------------------------

    def _service(self, service_id: uuid.UUID) -> Service:
        service = self.db.get(Service, service_id)
        if service is None:
            raise NotFound("Service")
        if service.status != RecordStatus.ACTIVE:
            raise BookingRejected("SERVICE_INACTIVE", f"{service.name} is not currently offered", status_code=422)
        return service

    def _resource(self, resource_id: uuid.UUID) -> Resource:
        resource = self.db.get(Resource, resource_id)
        if resource is None:
            raise NotFound("Resource")
        if resource.status != RecordStatus.ACTIVE:
            raise BookingRejected("RESOURCE_INACTIVE", f"{resource.name} is not accepting bookings")
        return resource

    def _lock_resources(self, resource_ids: Iterable[uuid.UUID]) -> None:
        """Serialize writers per resource. Rows are locked in id order to avoid deadlocks."""
        ids = sorted(set(resource_ids))
        if not ids:
            return
        rows = self.db.execute(
            select(Resource.id, Resource.status)
            .where(Resource.id.in_(ids))
            .order_by(Resource.id)
            .with_for_update()
        ).all()
        # Re-read status after the lock: the resource may have been deactivated
        # between the first validation and now.
        for _rid, status in rows:
            if status != RecordStatus.ACTIVE:
                self.db.rollback()
                raise BookingRejected("RESOURCE_INACTIVE", "A required resource is no longer accepting bookings")

    def _reload(self, booking_id: uuid.UUID) -> Booking:
        booking = self.db.execute(
            select(Booking).where(Booking.id == booking_id).execution_options(populate_existing=True)
        ).unique().scalar_one()
        return booking

    def get_visible_booking(self, booking_id: uuid.UUID) -> Booking:
        """Owners see their bookings; staff see all. Others get a 404, not a 403,
        so booking ids cannot be probed."""
        booking = self.db.get(Booking, booking_id)
        if booking is None:
            raise NotFound("Booking")
        if booking.user_id != self.actor.id and not has_permission(
            self.actor.role, Permission.BOOKINGS_VIEW_ALL
        ):
            raise NotFound("Booking")
        return booking

    def _get_modifiable(self, booking_id: uuid.UUID) -> Booking:
        booking = self.get_visible_booking(booking_id)
        if booking.user_id != self.actor.id and not self.can_manage:
            raise Forbidden("You cannot modify this booking")
        return booking

    def _locked(self, booking: Booking, extra_resource_ids: Iterable[uuid.UUID] = ()) -> Booking:
        ids = {booking.primary_resource_id, *(a.resource_id for a in booking.allocations), *extra_resource_ids}
        self._lock_resources(ids)
        return self._reload(booking.id)

    def _consumes(self, status: BookingStatus) -> bool:
        if status == S.PENDING:
            return self.rules.pending_consumes_capacity
        return status in {S.CONFIRMED, S.CONFLICTED, S.COMPLETED, S.NO_SHOW}

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            if "ex_booking_resources_no_overlap" in str(exc.orig):
                raise BookingRejected("CONFLICT", "This time overlaps another booking") from exc
            raise

    def _commit(self) -> None:
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if "ex_booking_resources_no_overlap" in str(exc.orig):
                raise BookingRejected("CONFLICT", "This time overlaps another booking") from exc
            raise

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _check(
        self,
        offering: Offering,
        start: datetime,
        *,
        quantity: int,
        override_rules: bool,
        exclude: Sequence[uuid.UUID] = (),
        ignore_notice: bool = False,
    ) -> SlotCheck:
        end = start + offering.duration
        snapshot = build_snapshot(
            self.db,
            offering.resource,
            start - offering.buffer_before,
            end + offering.buffer_after,
            self.rules,
            exclude_booking_ids=exclude,
        )
        return snapshot.check(
            offering,
            start,
            quantity=quantity,
            rules=self.rules,
            now=self.now,
            override_rules=override_rules,
            ignore_notice=ignore_notice,
        )

    def _evaluate(
        self,
        offering: Offering,
        extras: Sequence[Resource],
        start: datetime,
        *,
        user_id: uuid.UUID,
        quantity: int,
        override_rules: bool,
        join_waitlist: bool = False,
        exclude: Sequence[uuid.UUID] = (),
    ) -> bool:
        """Raise BookingRejected unless the booking can be made.

        Returns True when the booking should be waitlisted instead.
        """
        result = self._check(offering, start, quantity=quantity, override_rules=override_rules, exclude=exclude)
        waitlist = False
        if result.code == FULL and join_waitlist:
            if not self.rules.allow_waitlist:
                raise BookingRejected(FULL, "This session is full and the waitlist is disabled")
            waitlist = True
        elif not result.available:
            raise BookingRejected(
                result.code,
                result.message,
                details={"remaining": result.remaining, "capacity": result.capacity},
            )

        if not waitlist:
            # Every extra resource must be free for the same period, too.
            for extra in extras:
                extra_offering = replace(
                    offering, resource=extra, booking_type=BookingType.INDIVIDUAL, capacity=None
                )
                extra_result = self._check(
                    extra_offering, start, quantity=1, override_rules=override_rules, exclude=exclude
                )
                if not extra_result.available:
                    raise BookingRejected(
                        extra_result.code,
                        f"{extra.name}: {extra_result.message}",
                        details={"resource_id": str(extra.id)},
                    )

        if not offering.exclusive:
            same_session = select(Booking.id).where(
                Booking.user_id == user_id,
                Booking.primary_resource_id == offering.resource.id,
                Booking.service_id == offering.service.id,
                Booking.start_datetime == start,
                Booking.status.in_([*OPEN_STATUSES, S.WAITLISTED]),
            )
            if exclude:
                same_session = same_session.where(Booking.id.not_in(exclude))
            if self.db.scalar(same_session):
                raise BookingRejected("ALREADY_BOOKED", "You already have a booking for this session")
        return waitlist

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def _prepare(self, req: BookingRequest) -> tuple[User, Offering, list[Resource], datetime]:
        if req.override_rules and not self.can_override:
            raise Forbidden("Only administrators can override booking rules")
        if req.user_id != self.actor.id and not self.can_manage:
            raise Forbidden("You can only book for yourself")
        if req.quantity < 1:
            raise BookingRejected("INVALID_QUANTITY", "Quantity must be at least 1", status_code=422)

        user = self.db.get(User, req.user_id)
        if user is None:
            raise NotFound("User")
        if user.status != UserStatus.ACTIVE:
            raise BookingRejected("USER_INACTIVE", "This user account is not active", status_code=422)

        service = self._service(req.service_id)
        resource = self._resource(req.resource_id)
        extra_ids = [rid for rid in dict.fromkeys(req.additional_resource_ids) if rid != resource.id]
        if extra_ids and service.booking_type != BookingType.INDIVIDUAL:
            raise BookingRejected(
                "UNSUPPORTED",
                "Additional resources can only be attached to individual bookings",
                status_code=422,
            )
        extras = [self._resource(rid) for rid in extra_ids]
        offering = resolve_offering(self.db, service, resource, self.rules)
        start = ensure_aware(req.start, resource_timezone(resource, self.rules))
        return user, offering, extras, start

    def _insert(
        self,
        *,
        user_id: uuid.UUID,
        offering: Offering,
        extras: Sequence[Resource],
        start: datetime,
        quantity: int,
        notes: str | None,
        status: BookingStatus,
        rescheduled_from_id: uuid.UUID | None = None,
        series_id: uuid.UUID | None = None,
    ) -> Booking:
        booking_id = uuid.uuid4()
        end = start + offering.duration
        price = offering.price
        if price is not None and not offering.exclusive:
            price = price * quantity
        booking = Booking(
            id=booking_id,
            user_id=user_id,
            service_id=offering.service.id,
            primary_resource_id=offering.resource.id,
            location_id=offering.resource.location_id,
            start_datetime=start,
            end_datetime=end,
            quantity=quantity,
            status=status,
            notes=notes,
            price=price,
            created_by=self.actor.id,
            confirmed_at=self.now if status == S.CONFIRMED else None,
            rescheduled_from_id=rescheduled_from_id,
            recurring_series_id=series_id,
        )
        if status != S.WAITLISTED:
            self._allocate(booking, offering, extras, active=self._consumes(status))
        self.db.add(booking)
        return booking

    def _allocate(self, booking: Booking, offering: Offering, extras: Sequence[Resource], *, active: bool) -> None:
        occupied_start = booking.start_datetime - offering.buffer_before
        occupied_end = booking.end_datetime + offering.buffer_after
        key = offering.allocation_key(booking.id, booking.start_datetime)
        for resource in [offering.resource, *extras]:
            booking.allocations.append(
                BookingResource(
                    resource_id=resource.id,
                    occupied_start=occupied_start,
                    occupied_end=occupied_end,
                    allocation_key=key,
                    active=active,
                )
            )

    def _initial_status(self) -> BookingStatus:
        if self.rules.require_admin_confirmation and not self.can_manage:
            return S.PENDING
        return S.CONFIRMED

    def create(self, req: BookingRequest) -> Booking:
        user, offering, extras, start = self._prepare(req)
        check_kwargs = dict(
            user_id=user.id,
            quantity=req.quantity,
            override_rules=req.override_rules,
            join_waitlist=req.join_waitlist,
        )
        # 1st check: cheap rejection without taking any lock.
        self._evaluate(offering, extras, start, **check_kwargs)

        # Lock, then check again: this is the check that actually counts.
        self._lock_resources([offering.resource.id, *(e.id for e in extras)])
        waitlist = self._evaluate(offering, extras, start, **check_kwargs)

        status = S.WAITLISTED if waitlist else self._initial_status()
        booking = self._insert(
            user_id=user.id,
            offering=offering,
            extras=extras,
            start=start,
            quantity=req.quantity,
            notes=req.notes,
            status=status,
        )
        self._flush()
        audit.record(
            self.db,
            actor_id=self.actor.id,
            action="BOOKING_CREATED",
            entity_type="booking",
            entity_id=booking.id,
            new=audit.snapshot(booking, "user_id", "service_id", "primary_resource_id", "start_datetime", "end_datetime", "quantity", "status"),
        )
        if req.override_rules:
            audit.record(
                self.db,
                actor_id=self.actor.id,
                action="ADMIN_OVERRIDE",
                entity_type="booking",
                entity_id=booking.id,
                new={"override": "booking rules", "start": start},
            )
        events.publish(self.db, "booking.created", booking=booking)
        self._commit()
        return booking

    # ------------------------------------------------------------------
    # Cancel & waitlist
    # ------------------------------------------------------------------

    def cancel(self, booking_id: uuid.UUID, *, reason: str | None = None) -> Booking:
        booking = self._get_modifiable(booking_id)
        booking = self._locked(booking)

        if booking.status not in (*OPEN_STATUSES, S.WAITLISTED):
            raise BookingRejected("INVALID_STATUS_TRANSITION", f"A {booking.status.value.lower()} booking cannot be cancelled")

        policy_overridden = False
        if booking.status != S.WAITLISTED:
            deadline = booking.start_datetime - timedelta(minutes=self.rules.cancellation_window)
            if self.now > deadline or self.now >= booking.start_datetime:
                if not self.can_manage:
                    raise BookingRejected(
                        "CANCELLATION_WINDOW_PASSED",
                        "This booking can no longer be cancelled online",
                    )
                policy_overridden = True

        old_status = booking.status
        transition(booking, S.CANCELLED)
        booking.cancelled_at = self.now
        booking.cancelled_by = self.actor.id
        booking.cancellation_reason = reason
        released = any(a.active for a in booking.allocations)
        for allocation in booking.allocations:
            allocation.active = False
        self._flush()

        audit.record(
            self.db,
            actor_id=self.actor.id,
            action="BOOKING_CANCELLED",
            entity_type="booking",
            entity_id=booking.id,
            old={"status": old_status},
            new={"status": booking.status, "reason": reason},
        )
        if policy_overridden:
            audit.record(
                self.db,
                actor_id=self.actor.id,
                action="ADMIN_OVERRIDE",
                entity_type="booking",
                entity_id=booking.id,
                new={"override": "cancellation window"},
            )
        events.publish(self.db, "booking.cancelled", booking=booking, actor_id=self.actor.id)
        if released and booking.service.booking_type == BookingType.CAPACITY:
            self._promote_waitlist(booking.primary_resource, booking.service, booking.start_datetime)
        self._commit()
        return booking

    def _promote_waitlist(self, resource: Resource, service: Service, start: datetime) -> list[Booking]:
        """Move waitlisted bookings into freed capacity, strictly first come first served.

        Runs while the resource row is locked, so two cancellations can never
        promote two people into the same free seat.
        """
        if start <= self.now or resource.status != RecordStatus.ACTIVE:
            return []
        try:
            offering = resolve_offering(self.db, service, resource, self.rules)
        except BookingRejected:
            return []
        waiting = self.db.scalars(
            select(Booking)
            .where(
                Booking.primary_resource_id == resource.id,
                Booking.service_id == service.id,
                Booking.start_datetime == start,
                Booking.status == S.WAITLISTED,
            )
            .order_by(Booking.created_at, Booking.id)
        ).unique().all()

        promoted = []
        for candidate in waiting:
            result = self._check(offering, start, quantity=candidate.quantity, override_rules=False, ignore_notice=True)
            if not result.available:
                break
            transition(candidate, S.CONFIRMED)
            candidate.confirmed_at = self.now
            self._allocate(candidate, offering, [], active=True)
            self._flush()
            audit.record(
                self.db,
                actor_id=self.actor.id,
                action="WAITLIST_PROMOTED",
                entity_type="booking",
                entity_id=candidate.id,
                old={"status": S.WAITLISTED},
                new={"status": S.CONFIRMED},
            )
            events.publish(self.db, "booking.waitlist_promoted", booking=candidate)
            promoted.append(candidate)
        return promoted

    # ------------------------------------------------------------------
    # Reschedule / reassign
    # ------------------------------------------------------------------

    def reschedule(
        self,
        booking_id: uuid.UUID,
        *,
        new_start: datetime | None = None,
        new_resource_id: uuid.UUID | None = None,
        override_rules: bool = False,
        reason: str | None = None,
        audit_action: str = "BOOKING_RESCHEDULED",
    ) -> Booking:
        if override_rules and not self.can_override:
            raise Forbidden("Only administrators can override booking rules")
        booking = self._get_modifiable(booking_id)
        if booking.status not in OPEN_STATUSES:
            raise BookingRejected("INVALID_STATUS_TRANSITION", f"A {booking.status.value.lower()} booking cannot be rescheduled")

        target = self._resource(new_resource_id or booking.primary_resource_id)
        service = self.db.get(Service, booking.service_id)
        assert service is not None
        offering = resolve_offering(self.db, service, target, self.rules)
        start = (
            ensure_aware(new_start, resource_timezone(target, self.rules))
            if new_start is not None
            else booking.start_datetime
        )
        if start == booking.start_datetime and target.id == booking.primary_resource_id:
            raise BookingRejected("NO_CHANGE", "Choose a different time or resource", status_code=422)

        policy_overridden = False
        deadline = booking.start_datetime - timedelta(minutes=self.rules.rescheduling_window)
        if self.now > deadline or self.now >= booking.start_datetime:
            if not self.can_manage:
                raise BookingRejected(
                    "RESCHEDULING_WINDOW_PASSED", "This booking can no longer be rescheduled online"
                )
            policy_overridden = True

        extras_ids = [
            a.resource_id for a in booking.allocations if a.resource_id != booking.primary_resource_id
        ]
        extras = [self._resource(rid) for rid in dict.fromkeys(extras_ids) if rid != target.id]
        check_kwargs = dict(
            user_id=booking.user_id,
            quantity=booking.quantity,
            override_rules=override_rules,
            exclude=[booking.id],
        )
        self._evaluate(offering, extras, start, **check_kwargs)

        booking = self._locked(booking, [target.id, *(e.id for e in extras)])
        if booking.status not in OPEN_STATUSES:
            raise BookingRejected("INVALID_STATUS_TRANSITION", "This booking changed while you were editing it")
        # Validate the new slot BEFORE touching the existing reservation.
        self._evaluate(offering, extras, start, **check_kwargs)

        old_start, old_resource, old_status = booking.start_datetime, booking.primary_resource, booking.status
        released = any(a.active for a in booking.allocations)
        # Release the old allocation and write the replacement in the same
        # transaction; if anything below fails the whole change rolls back.
        for allocation in booking.allocations:
            allocation.active = False
        transition(booking, S.RESCHEDULED)
        self._flush()

        new_status = S.PENDING if old_status == S.PENDING else S.CONFIRMED
        replacement = self._insert(
            user_id=booking.user_id,
            offering=offering,
            extras=extras,
            start=start,
            quantity=booking.quantity,
            notes=booking.notes,
            status=new_status,
            rescheduled_from_id=booking.id,
            series_id=booking.recurring_series_id,
        )
        self._flush()

        audit.record(
            self.db,
            actor_id=self.actor.id,
            action=audit_action,
            entity_type="booking",
            entity_id=booking.id,
            old={"start": old_start, "resource_id": old_resource.id, "status": old_status},
            new={"start": start, "resource_id": target.id, "booking_id": replacement.id, "reason": reason},
        )
        if override_rules or policy_overridden:
            audit.record(
                self.db,
                actor_id=self.actor.id,
                action="ADMIN_OVERRIDE",
                entity_type="booking",
                entity_id=replacement.id,
                new={"override": "booking rules" if override_rules else "rescheduling window"},
            )
        events.publish(
            self.db,
            "booking.rescheduled",
            booking=replacement,
            previous=booking,
            resource_changed=target.id != old_resource.id,
        )
        if released and service.booking_type == BookingType.CAPACITY:
            self._promote_waitlist(old_resource, service, old_start)
        self._commit()
        return replacement

    def reassign(self, booking_id: uuid.UUID, resource_id: uuid.UUID, *, override_rules: bool = False, reason: str | None = None) -> Booking:
        if not self.can_manage:
            raise Forbidden()
        return self.reschedule(
            booking_id,
            new_resource_id=resource_id,
            override_rules=override_rules,
            reason=reason,
            audit_action="BOOKING_REASSIGNED",
        )

    # ------------------------------------------------------------------
    # Admin status changes
    # ------------------------------------------------------------------

    def _require_manage(self) -> None:
        if not self.can_manage:
            raise Forbidden()

    def confirm(self, booking_id: uuid.UUID) -> Booking:
        self._require_manage()
        booking = self._locked(self.get_visible_booking(booking_id))
        old = booking.status
        if booking.status == S.WAITLISTED:
            raise BookingRejected("INVALID_STATUS_TRANSITION", "Waitlisted bookings are confirmed automatically when a place frees up")
        if booking.status == S.CONFLICTED:
            return self.resolve_override(booking_id)
        inactive = [a for a in booking.allocations if not a.active]
        if inactive:
            # The pending booking did not hold its slot; make sure it is still free.
            resource = booking.primary_resource
            offering = resolve_offering(self.db, booking.service, resource, self.rules)
            extras = [self.db.get(Resource, a.resource_id) for a in booking.allocations if a.resource_id != resource.id]
            self._evaluate(
                offering,
                [e for e in extras if e is not None],
                booking.start_datetime,
                user_id=booking.user_id,
                quantity=booking.quantity,
                override_rules=True,
                exclude=[booking.id],
            )
            for allocation in booking.allocations:
                allocation.active = True
        transition(booking, S.CONFIRMED)
        booking.confirmed_at = self.now
        self._flush()
        audit.record(self.db, actor_id=self.actor.id, action="BOOKING_CONFIRMED", entity_type="booking", entity_id=booking.id, old={"status": old}, new={"status": booking.status})
        events.publish(self.db, "booking.confirmed", booking=booking)
        self._commit()
        return booking

    def _finish(self, booking_id: uuid.UUID, status: BookingStatus, action: str) -> Booking:
        self._require_manage()
        booking = self._locked(self.get_visible_booking(booking_id))
        if booking.start_datetime > self.now:
            raise BookingRejected("NOT_STARTED", "This booking has not started yet", status_code=422)
        old = booking.status
        transition(booking, status)
        if status == S.COMPLETED:
            booking.completed_at = self.now
        self._flush()
        audit.record(self.db, actor_id=self.actor.id, action=action, entity_type="booking", entity_id=booking.id, old={"status": old}, new={"status": status})
        self._commit()
        return booking

    def complete(self, booking_id: uuid.UUID) -> Booking:
        return self._finish(booking_id, S.COMPLETED, "BOOKING_COMPLETED")

    def mark_no_show(self, booking_id: uuid.UUID) -> Booking:
        return self._finish(booking_id, S.NO_SHOW, "BOOKING_NO_SHOW")

    def resolve_override(self, booking_id: uuid.UUID, *, note: str | None = None) -> Booking:
        """Keep a conflicted booking as-is, overriding the schedule change."""
        if not self.can_override:
            raise Forbidden()
        booking = self._locked(self.get_visible_booking(booking_id))
        if booking.status != S.CONFLICTED:
            raise BookingRejected("NOT_CONFLICTED", "Only conflicted bookings can be overridden")
        transition(booking, S.CONFIRMED)
        booking.conflict_reason = None
        self._flush()
        audit.record(
            self.db,
            actor_id=self.actor.id,
            action="CONFLICT_RESOLVED",
            entity_type="booking",
            entity_id=booking.id,
            old={"status": S.CONFLICTED},
            new={"status": S.CONFIRMED, "resolution": "override", "note": note},
        )
        self._commit()
        return booking

    # ------------------------------------------------------------------
    # Recurring bookings
    # ------------------------------------------------------------------

    def _recurring_setup(self, req: BookingRequest, spec: RecurrenceSpec) -> tuple[User, Offering, list[datetime]]:
        if not self.rules.allow_recurring_bookings and not self.can_manage:
            raise BookingRejected("RECURRING_DISABLED", "Recurring bookings are not enabled", status_code=422)
        if spec.interval < 1 or spec.count < 1:
            raise BookingRejected("INVALID_RECURRENCE", "Interval and count must be at least 1", status_code=422)
        if req.additional_resource_ids:
            raise BookingRejected("UNSUPPORTED", "Recurring bookings support a single resource", status_code=422)
        user, offering, _, start = self._prepare(req)
        tz = resource_timezone(offering.resource, self.rules)
        first_local = start.astimezone(tz).replace(tzinfo=None)
        starts = [localize(s, tz) for s in expand_recurrence(first_local, spec)]
        if len(starts) > self.rules.max_recurring_occurrences:
            raise BookingRejected(
                "TOO_MANY_OCCURRENCES",
                f"At most {self.rules.max_recurring_occurrences} occurrences are allowed",
                status_code=422,
            )
        return user, offering, starts

    def _evaluate_occurrence(self, user: User, offering: Offering, start: datetime, req: BookingRequest) -> OccurrenceResult:
        try:
            self._evaluate(
                offering,
                [],
                start,
                user_id=user.id,
                quantity=req.quantity,
                override_rules=req.override_rules,
            )
        except BookingRejected as exc:
            return OccurrenceResult(start, start + offering.duration, exc.code, exc.message)
        return OccurrenceResult(start, start + offering.duration, AVAILABLE, "Available")

    def preview_recurring(self, req: BookingRequest, spec: RecurrenceSpec) -> list[OccurrenceResult]:
        user, offering, starts = self._recurring_setup(req, spec)
        return [self._evaluate_occurrence(user, offering, s, req) for s in starts]

    def create_recurring(
        self, req: BookingRequest, spec: RecurrenceSpec, *, skip_conflicts: bool
    ) -> tuple[RecurringSeries, list[Booking], list[OccurrenceResult]]:
        """Each occurrence is validated independently. Conflicts are returned
        instead of silently dropped unless the caller chose to skip them."""
        user, offering, starts = self._recurring_setup(req, spec)
        self._lock_resources([offering.resource.id])
        results = [self._evaluate_occurrence(user, offering, s, req) for s in starts]
        conflicts = [r for r in results if not r.available]
        if conflicts and not skip_conflicts:
            self.db.rollback()
            raise BookingRejected(
                "RECURRING_CONFLICTS",
                f"{len(conflicts)} of {len(results)} occurrences are unavailable",
                details=[
                    {"start": r.start.isoformat(), "code": r.code, "message": r.message} for r in conflicts
                ],
            )
        available = [r for r in results if r.available]
        if not available:
            self.db.rollback()
            raise BookingRejected("NO_AVAILABLE_OCCURRENCES", "None of the occurrences are available")

        series = RecurringSeries(
            user_id=user.id,
            service_id=offering.service.id,
            resource_id=offering.resource.id,
            frequency=None if spec.custom_starts else spec.frequency,
            interval=spec.interval,
            occurrences=len(results),
            first_start=results[0].start,
            created_by=self.actor.id,
        )
        self.db.add(series)
        self._flush()
        status = self._initial_status()
        created = []
        for occurrence in available:
            booking = self._insert(
                user_id=user.id,
                offering=offering,
                extras=[],
                start=occurrence.start,
                quantity=req.quantity,
                notes=req.notes,
                status=status,
                series_id=series.id,
            )
            # Flush per occurrence so each one's allocation is visible to the
            # next (occurrences of a series must not overlap each other).
            self._flush()
            created.append(booking)
        audit.record(
            self.db,
            actor_id=self.actor.id,
            action="RECURRING_BOOKING_CREATED",
            entity_type="recurring_series",
            entity_id=series.id,
            new={"created": len(created), "skipped": len(conflicts), "first_start": results[0].start},
        )
        for booking in created:
            events.publish(self.db, "booking.created", booking=booking)
        self._commit()
        return series, created, conflicts


def suggest_alternatives(
    db: Session,
    *,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    start: datetime,
    quantity: int = 1,
    limit: int = 6,
    now: datetime | None = None,
) -> list[tuple[Resource, SlotCheck]]:
    """Nearby options when the requested slot is gone: the same time on
    another resource, or the closest free times on any suitable resource.

    An enhancement on top of the booking guarantees, not part of them: a
    suggestion is still fully re-validated when it is booked.
    """
    rules = settings_service.get_rules(db)
    now = now or utcnow()
    service = db.get(Service, service_id)
    requested = db.get(Resource, resource_id)
    if service is None or service.status != RecordStatus.ACTIVE:
        raise NotFound("Service")
    if requested is None:
        raise NotFound("Resource")
    start = ensure_aware(start, resource_timezone(requested, rules))
    candidates = db.scalars(
        select(Resource).where(
            Resource.status == RecordStatus.ACTIVE,
            Resource.service_links.any(service_id=service.id, status=RecordStatus.ACTIVE),
        )
    ).unique().all()

    options: list[tuple[float, int, Resource, SlotCheck]] = []
    for resource in candidates:
        offering = resolve_offering(db, service, resource, rules)
        day = start.astimezone(resource_timezone(resource, rules)).date()
        for d in (day, day + timedelta(days=1)):
            _, slots = generate_slots(db, offering, d, rules, now, quantity=quantity)
            for slot in slots:
                if not slot.available or (resource.id == requested.id and slot.start == start):
                    continue
                distance = abs((slot.start - start).total_seconds())
                options.append((distance, 0 if resource.id == requested.id else 1, resource, slot))
    options.sort(key=lambda o: (o[0], o[1], o[2].name))
    return [(resource, slot) for _, _, resource, slot in options[:limit]]


# ----------------------------------------------------------------------
# Schedule changes and conflict detection
# ----------------------------------------------------------------------


def booking_summary(booking: Booking) -> dict[str, Any]:
    return {
        "id": str(booking.id),
        "user_name": booking.user.name,
        "user_email": booking.user.email,
        "service_name": booking.service.name,
        "resource_name": booking.primary_resource.name,
        "start_datetime": booking.start_datetime.isoformat(),
        "end_datetime": booking.end_datetime.isoformat(),
        "status": booking.status.value,
    }


def find_affected_bookings(
    db: Session, *, resource_ids: Iterable[uuid.UUID] | None, now: datetime
) -> list[AffectedBooking]:
    """Upcoming active bookings that the *current* schedule no longer allows."""
    rules = settings_service.get_rules(db)
    stmt = (
        select(BookingResource, Booking)
        .join(Booking, Booking.id == BookingResource.booking_id)
        .where(
            Booking.status.in_([S.PENDING, S.CONFIRMED]),
            Booking.end_datetime > now,
        )
    )
    if resource_ids is not None:
        stmt = stmt.where(BookingResource.resource_id.in_(list(resource_ids)))
    rows = db.execute(stmt).unique().all()

    by_resource: dict[uuid.UUID, list[Booking]] = {}
    for allocation, booking in rows:
        by_resource.setdefault(allocation.resource_id, []).append(booking)

    affected: dict[uuid.UUID, AffectedBooking] = {}
    for rid, bookings in by_resource.items():
        resource = db.get(Resource, rid)
        if resource is None:
            continue
        if resource.status != RecordStatus.ACTIVE:
            for b in bookings:
                affected.setdefault(b.id, AffectedBooking(b.id, "RESOURCE_INACTIVE", f"{resource.name} is inactive", booking_summary(b)))
            continue
        first = min(b.start_datetime for b in bookings)
        last = max(b.end_datetime for b in bookings)
        snapshot = build_snapshot(db, resource, first, last, rules, include_occupancy=False)
        for b in bookings:
            violation = snapshot.schedule_violation(b.start_datetime, b.end_datetime)
            if violation:
                code, detail = violation
                message = detail or REASON_MESSAGES[code]
                if resource.id != b.primary_resource_id:
                    message = f"{resource.name}: {message}"
                affected.setdefault(b.id, AffectedBooking(b.id, code, message, booking_summary(b)))
    return sorted(affected.values(), key=lambda a: a.summary["start_datetime"])


CONFLICT_ACTIONS = ("keep", "mark_conflicted", "cancel")


@dataclass
class ScheduleChangeResult:
    applied: bool
    affected: list[AffectedBooking]
    result: Any = None


def apply_schedule_change(
    db: Session,
    actor: User,
    *,
    mutate: Callable[[], Any],
    resource_ids: Iterable[uuid.UUID] | None,
    dry_run: bool,
    conflict_action: str,
    audit_action: str,
    entity_type: str,
    entity_id: Any = None,
    audit_new: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ScheduleChangeResult:
    """Apply an availability-affecting change and deal with bookings it breaks.

    With ``dry_run`` the change is applied inside a savepoint, the affected
    bookings are collected, and everything is rolled back -- so admins see
    "This change affects N bookings" computed by the exact same rules.
    """
    if conflict_action not in CONFLICT_ACTIONS:
        raise BookingRejected("INVALID_ACTION", f"conflict_action must be one of {', '.join(CONFLICT_ACTIONS)}", status_code=422)
    now = now or utcnow()
    scope = None if resource_ids is None else sorted(set(resource_ids))

    # Hold the resource locks so no booking can slip in between the scan and commit.
    lock = select(Resource.id).order_by(Resource.id).with_for_update()
    if scope is not None:
        lock = lock.where(Resource.id.in_(scope))
    db.execute(lock)

    savepoint = db.begin_nested()
    result = mutate()
    db.flush()
    affected = find_affected_bookings(db, resource_ids=scope, now=now)

    if dry_run:
        savepoint.rollback()
        db.rollback()
        return ScheduleChangeResult(applied=False, affected=affected)

    savepoint.commit()
    audit.record(
        db,
        actor_id=actor.id,
        action=audit_action,
        entity_type=entity_type,
        entity_id=entity_id if entity_id is not None else getattr(result, "id", None),
        new={**(audit_new or {}), "affected_bookings": len(affected), "conflict_action": conflict_action},
    )
    for item in affected:
        booking = db.get(Booking, item.booking_id)
        if booking is None:
            continue
        if conflict_action == "mark_conflicted":
            transition(booking, S.CONFLICTED)
            booking.conflict_reason = item.message
            audit.record(db, actor_id=actor.id, action="BOOKING_CONFLICTED", entity_type="booking", entity_id=booking.id, new={"reason": item.message})
            events.publish(db, "booking.conflicted", booking=booking)
        elif conflict_action == "cancel":
            transition(booking, S.CANCELLED)
            booking.cancelled_at = now
            booking.cancelled_by = actor.id
            booking.cancellation_reason = f"Schedule change: {item.message}"
            for allocation in booking.allocations:
                allocation.active = False
            audit.record(db, actor_id=actor.id, action="BOOKING_CANCELLED", entity_type="booking", entity_id=booking.id, new={"reason": booking.cancellation_reason})
            events.publish(db, "booking.cancelled", booking=booking, actor_id=actor.id)
    db.commit()
    return ScheduleChangeResult(applied=True, affected=affected, result=result)

