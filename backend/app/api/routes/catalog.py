"""Public browsing: services, resources, locations and availability."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select

from app.api.deps import DB, get_current_user
from app.api.serializers import offering_out, resource_out
from app.core.errors import NotFound
from app.core.timeutils import utcnow
from app.models import Location, Resource, ResourceService, Service
from app.models.enums import RecordStatus, ResourceType
from app.schemas import (
    AggregatedSlotOut,
    AlternativeOut,
    AvailabilityOut,
    AvailabilityRuleOut,
    LocationOut,
    PublicConfigOut,
    ResourceDetailOut,
    ResourceOut,
    ResourceSlotsOut,
    ServiceDetailOut,
    ServiceOut,
    SlotOut,
)
from app.services import settings_service
from app.services.availability import generate_slots, resolve_offering, resource_timezone
from app.services.booking_engine import BookingEngine, suggest_alternatives

router = APIRouter(prefix="/api", tags=["catalog"])


def _active_links(links: list[ResourceService]) -> list[ResourceService]:
    return [
        link
        for link in links
        if link.status == RecordStatus.ACTIVE
        and link.service.status == RecordStatus.ACTIVE
        and link.resource.status == RecordStatus.ACTIVE
    ]


@router.get("/config", response_model=PublicConfigOut)
def public_config(db: DB) -> PublicConfigOut:
    rules = settings_service.get_rules(db)
    return PublicConfigOut(**{k: getattr(rules, k) for k in PublicConfigOut.model_fields})


@router.get("/locations", response_model=list[LocationOut])
def list_locations(db: DB) -> list[Location]:
    return list(db.scalars(select(Location).where(Location.status == RecordStatus.ACTIVE).order_by(Location.name)))


@router.get("/services", response_model=list[ServiceOut])
def list_services(
    db: DB,
    q: str | None = Query(default=None, max_length=100),
    resource_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    max_price: Decimal | None = Query(default=None, ge=0),
) -> list[Service]:
    stmt = select(Service).where(Service.status == RecordStatus.ACTIVE)
    if q:
        stmt = stmt.where(or_(Service.name.ilike(f"%{q}%"), Service.description.ilike(f"%{q}%")))
    if max_price is not None:
        stmt = stmt.where(or_(Service.price.is_(None), Service.price <= max_price))
    if resource_id or location_id:
        link = select(ResourceService.service_id).join(Resource).where(
            ResourceService.status == RecordStatus.ACTIVE, Resource.status == RecordStatus.ACTIVE
        )
        if resource_id:
            link = link.where(ResourceService.resource_id == resource_id)
        if location_id:
            link = link.where(Resource.location_id == location_id)
        stmt = stmt.where(Service.id.in_(link))
    return list(db.scalars(stmt.order_by(Service.name)))


@router.get("/services/{service_id}", response_model=ServiceDetailOut)
def get_service(service_id: uuid.UUID, db: DB) -> ServiceDetailOut:
    service = db.get(Service, service_id)
    if service is None or service.status != RecordStatus.ACTIVE:
        raise NotFound("Service")
    return ServiceDetailOut(
        **ServiceOut.model_validate(service).model_dump(),
        resources=[offering_out(link) for link in _active_links(service.resource_links)],
    )


@router.get("/resources", response_model=list[ResourceOut])
def list_resources(
    db: DB,
    q: str | None = Query(default=None, max_length=100),
    type: ResourceType | None = None,
    location_id: uuid.UUID | None = None,
    service_id: uuid.UUID | None = None,
) -> list[ResourceOut]:
    rules = settings_service.get_rules(db)
    stmt = select(Resource).where(Resource.status == RecordStatus.ACTIVE)
    if q:
        stmt = stmt.where(or_(Resource.name.ilike(f"%{q}%"), Resource.description.ilike(f"%{q}%")))
    if type:
        stmt = stmt.where(Resource.type == type)
    if location_id:
        stmt = stmt.where(Resource.location_id == location_id)
    if service_id:
        stmt = stmt.where(
            Resource.service_links.any(service_id=service_id, status=RecordStatus.ACTIVE)
        )
    return [resource_out(r, rules) for r in db.scalars(stmt.order_by(Resource.name)).unique()]


@router.get("/resources/{resource_id}", response_model=ResourceDetailOut)
def get_resource(resource_id: uuid.UUID, db: DB) -> ResourceDetailOut:
    rules = settings_service.get_rules(db)
    resource = db.get(Resource, resource_id)
    if resource is None or resource.status != RecordStatus.ACTIVE:
        raise NotFound("Resource")
    return ResourceDetailOut(
        **resource_out(resource, rules).model_dump(),
        services=[offering_out(link) for link in _active_links(resource.service_links)],
        availability=[
            AvailabilityRuleOut.model_validate(r)
            for r in sorted(resource.availability_rules, key=lambda r: (r.day_of_week, r.start_time))
        ],
    )


def _slot_out(slot, tz) -> SlotOut:
    start, end = slot.start.astimezone(tz), slot.end.astimezone(tz)
    return SlotOut(
        start=start,
        end=end,
        start_time=start.strftime("%H:%M"),
        end_time=end.strftime("%H:%M"),
        available=slot.available,
        status=slot.code,
        message=slot.message,
        remaining=slot.remaining,
        capacity=slot.capacity,
    )


def _booking_being_moved(request: Request, db: DB, exclude_booking_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Only someone who can see a booking may leave it out of availability."""
    if exclude_booking_id is None:
        return []
    actor = get_current_user(request, db)
    return [BookingEngine(db, actor).get_visible_booking(exclude_booking_id).id]


BookingBeingMoved = Annotated[list[uuid.UUID], Depends(_booking_being_moved)]


@router.get("/availability", response_model=AvailabilityOut)
def availability(
    db: DB,
    service_id: uuid.UUID,
    date: date,
    exclude: BookingBeingMoved,
    resource_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    quantity: int = Query(default=1, ge=1, le=1000),
) -> AvailabilityOut:
    """Slots for one service on one date.

    With ``resource_id``: that resource's slots (Service -> Resource -> Date).
    Without it: every suitable resource, plus an aggregated view of which
    resources are free at each time (Service -> Date -> Resources).
    With ``exclude_booking_id``: the booking a reschedule is moving does not
    count as a clash, just as when the move itself is validated.
    """
    rules = settings_service.get_rules(db)
    service = db.get(Service, service_id)
    if service is None or service.status != RecordStatus.ACTIVE:
        raise NotFound("Service")
    stmt = select(Resource).where(
        Resource.status == RecordStatus.ACTIVE,
        Resource.service_links.any(service_id=service.id, status=RecordStatus.ACTIVE),
    )
    if resource_id:
        stmt = stmt.where(Resource.id == resource_id)
    if location_id:
        stmt = stmt.where(Resource.location_id == location_id)
    resources = db.scalars(stmt.order_by(Resource.name)).unique().all()
    if resource_id and not resources:
        raise NotFound("Resource")

    now = utcnow()
    per_resource: list[ResourceSlotsOut] = []
    aggregate: dict[datetime, AggregatedSlotOut] = {}
    for resource in resources:
        offering = resolve_offering(db, service, resource, rules)
        tz, slots = generate_slots(db, offering, date, rules, now, quantity=quantity, exclude_booking_ids=exclude)
        outs = [_slot_out(s, tz) for s in slots]
        per_resource.append(
            ResourceSlotsOut(
                resource_id=resource.id,
                resource_name=resource.name,
                resource_type=resource.type,
                timezone=tz.key,
                duration_minutes=int(offering.duration.total_seconds() // 60),
                price=offering.price,
                booking_type=offering.booking_type,
                slots=outs,
            )
        )
        for s in outs:
            agg = aggregate.setdefault(
                s.start,
                AggregatedSlotOut(
                    start=s.start, end=s.end, start_time=s.start_time, end_time=s.end_time, available=False, resource_ids=[]
                ),
            )
            if s.available:
                agg.available = True
                agg.resource_ids.append(resource.id)
    return AvailabilityOut(
        date=date,
        service_id=service.id,
        resources=per_resource,
        slots=sorted(aggregate.values(), key=lambda s: s.start),
    )


@router.get("/resources/{resource_id}/availability", response_model=AvailabilityOut)
def resource_availability(
    resource_id: uuid.UUID, service_id: uuid.UUID, date: date, db: DB, quantity: int = Query(default=1, ge=1)
) -> AvailabilityOut:
    return availability(db, service_id=service_id, date=date, exclude=[], resource_id=resource_id, quantity=quantity)


@router.get("/availability/alternatives", response_model=list[AlternativeOut])
def alternatives(
    db: DB,
    service_id: uuid.UUID,
    resource_id: uuid.UUID,
    start: datetime,
    quantity: int = Query(default=1, ge=1),
) -> list[AlternativeOut]:
    """Enhancement: suggestions when the requested slot is unavailable."""
    rules = settings_service.get_rules(db)
    options = suggest_alternatives(
        db, service_id=service_id, resource_id=resource_id, start=start, quantity=quantity
    )
    return [
        AlternativeOut(
            resource_id=r.id,
            resource_name=r.name,
            timezone=resource_timezone(r, rules).key,
            start=slot.start.astimezone(resource_timezone(r, rules)),
            end=slot.end.astimezone(resource_timezone(r, rules)),
            same_resource=r.id == resource_id,
            remaining=slot.remaining,
        )
        for r, slot in options
    ]
