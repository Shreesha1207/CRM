"""ORM -> response model conversion, batched to avoid N+1 queries on lists."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.maps import google_maps_url
from app.core.permissions import Permission, has_permission
from app.models import Booking, Location, Resource, ResourceService, User
from app.models.enums import BookingStatus, BookingType
from app.schemas import (
    BookingOut,
    LocationOut,
    LocationRef,
    OfferingOut,
    ResourceOut,
    ResourceRef,
    ServiceRef,
    UserRef,
)
from app.services.availability import resource_timezone
from app.services.booking_engine import OPEN_STATUSES
from app.services.settings_service import BookingRules


def resource_out(resource: Resource, rules: BookingRules) -> ResourceOut:
    return ResourceOut(
        id=resource.id,
        name=resource.name,
        description=resource.description,
        type=resource.type,
        capacity=resource.capacity,
        metadata=resource.attributes or {},
        location_id=resource.location_id,
        location=LocationOut.model_validate(resource.location) if resource.location else None,
        timezone=resource_timezone(resource, rules).key,
        status=resource.status,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
    )


def location_ref(location: Location | None) -> LocationRef | None:
    if location is None:
        return None
    return LocationRef(
        id=location.id,
        name=location.name,
        address=location.address,
        google_maps_url=google_maps_url(location.name, location.address, location.map_url),
    )


def offering_out(link: ResourceService) -> OfferingOut:
    service, resource = link.service, link.resource
    return OfferingOut(
        resource_id=resource.id,
        resource_name=resource.name,
        resource_type=resource.type,
        service_id=service.id,
        service_name=service.name,
        booking_type=service.booking_type,
        duration_minutes=link.custom_duration or service.duration_minutes,
        max_duration_minutes=max(
            link.custom_duration or service.duration_minutes,
            (service.max_duration_minutes or 0) if service.booking_type == BookingType.INDIVIDUAL else 0,
        ),
        price=link.custom_price if link.custom_price is not None else service.price,
        custom_duration=link.custom_duration,
        custom_price=link.custom_price,
        custom_buffer_before=link.custom_buffer_before,
        custom_buffer_after=link.custom_buffer_after,
        location_name=resource.location.name if resource.location else None,
        location=location_ref(resource.location),
        status=link.status,
    )


def booking_permissions(booking: Booking, actor: User, rules: BookingRules, now: datetime) -> tuple[bool, bool]:
    manage = has_permission(actor.role, Permission.BOOKINGS_MANAGE)
    mine = booking.user_id == actor.id
    if not (mine or manage):
        return False, False
    if booking.status == BookingStatus.WAITLISTED:
        return (manage or booking.start_datetime > now), False
    if booking.status not in OPEN_STATUSES:
        return False, False
    if manage:
        return True, True
    started = now >= booking.start_datetime
    can_cancel = not started and now <= booking.start_datetime - timedelta(minutes=rules.cancellation_window)
    can_reschedule = not started and now <= booking.start_datetime - timedelta(minutes=rules.rescheduling_window)
    return can_cancel, can_reschedule


def bookings_out(
    db: Session, bookings: Sequence[Booking], actor: User, rules: BookingRules, now: datetime
) -> list[BookingOut]:
    if not bookings:
        return []
    ids = [b.id for b in bookings]

    successors = dict(
        db.execute(
            select(Booking.rescheduled_from_id, Booking.id).where(Booking.rescheduled_from_id.in_(ids))
        ).all()
    )

    extra_ids = {
        a.resource_id for b in bookings for a in b.allocations if a.resource_id != b.primary_resource_id
    }
    extras: dict[uuid.UUID, Resource] = (
        {r.id: r for r in db.scalars(select(Resource).where(Resource.id.in_(extra_ids))).unique()}
        if extra_ids
        else {}
    )

    positions: dict[uuid.UUID, int] = {}
    for b in bookings:
        if b.status == BookingStatus.WAITLISTED:
            positions[b.id] = 1 + (
                db.scalar(
                    select(func.count())
                    .select_from(Booking)
                    .where(
                        Booking.primary_resource_id == b.primary_resource_id,
                        Booking.service_id == b.service_id,
                        Booking.start_datetime == b.start_datetime,
                        Booking.status == BookingStatus.WAITLISTED,
                        (Booking.created_at < b.created_at)
                        | ((Booking.created_at == b.created_at) & (Booking.id < b.id)),
                    )
                )
                or 0
            )

    out = []
    for b in bookings:
        resource = b.primary_resource
        can_cancel, can_reschedule = booking_permissions(b, actor, rules, now)
        seen: set[uuid.UUID] = set()
        additional = []
        for a in b.allocations:
            if a.resource_id != b.primary_resource_id and a.resource_id not in seen and a.resource_id in extras:
                seen.add(a.resource_id)
                r = extras[a.resource_id]
                additional.append(ResourceRef(id=r.id, name=r.name, type=r.type))
        out.append(
            BookingOut(
                id=b.id,
                user=UserRef(id=b.user.id, name=b.user.name, email=b.user.email),
                service=ServiceRef(
                    id=b.service.id,
                    name=b.service.name,
                    booking_type=b.service.booking_type,
                    duration_minutes=b.service.duration_minutes,
                ),
                resource=ResourceRef(id=resource.id, name=resource.name, type=resource.type),
                additional_resources=additional,
                location=location_ref(resource.location),
                timezone=resource_timezone(resource, rules).key,
                start_datetime=b.start_datetime,
                end_datetime=b.end_datetime,
                quantity=b.quantity,
                status=b.status,
                notes=b.notes,
                price=b.price,
                created_at=b.created_at,
                updated_at=b.updated_at,
                confirmed_at=b.confirmed_at,
                cancelled_at=b.cancelled_at,
                cancellation_reason=b.cancellation_reason,
                conflict_reason=b.conflict_reason,
                rescheduled_from_id=b.rescheduled_from_id,
                rescheduled_to_id=successors.get(b.id),
                recurring_series_id=b.recurring_series_id,
                waitlist_position=positions.get(b.id),
                can_cancel=can_cancel,
                can_reschedule=can_reschedule,
            )
        )
    return out


def booking_out(db: Session, booking: Booking, actor: User, rules: BookingRules, now: datetime) -> BookingOut:
    return bookings_out(db, [booking], actor, rules, now)[0]
