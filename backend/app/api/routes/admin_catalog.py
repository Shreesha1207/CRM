"""Admin management of locations, resources, services and their links.

Any change that can shrink availability (deactivating a resource, editing
its weekly schedule, moving it to another location...) goes through
`apply_schedule_change`, which supports `dry_run=true` to preview which
existing bookings would be affected before anything is saved.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, exists, or_, select

from app.api.deps import DB
from app.api.deps import require as require_permission
from app.api.serializers import offering_out, resource_out
from app.core.errors import AppError, NotFound
from app.core.permissions import Permission
from app.models import (
    Booking,
    BookingResource,
    Location,
    Resource,
    ResourceAvailability,
    ResourceService,
    Service,
    User,
)
from app.models.enums import RecordStatus, ResourceType
from app.schemas import (
    AffectedBookingOut,
    AvailabilityRuleIn,
    AvailabilityRuleOut,
    ConflictAction,
    LocationIn,
    LocationOut,
    OfferingOut,
    ResourceDetailOut,
    ResourceIn,
    ResourceOut,
    ResourceServiceIn,
    ScheduleChangeOut,
    ServiceDetailOut,
    ServiceIn,
    ServiceOut,
)
from app.services import audit, settings_service
from app.services.booking_engine import ScheduleChangeResult, apply_schedule_change

router = APIRouter(prefix="/api/admin", tags=["admin: catalog"])

ManageResources = Depends(require_permission(Permission.RESOURCES_MANAGE))
ManageServices = Depends(require_permission(Permission.SERVICES_MANAGE))
ManageLocations = Depends(require_permission(Permission.LOCATIONS_MANAGE))
ManageSchedules = Depends(require_permission(Permission.SCHEDULES_MANAGE))

RESOURCE_COLUMNS = ("name", "description", "type", "capacity", "attributes", "location_id", "status")
SERVICE_COLUMNS = (
    "name", "description", "duration_minutes", "price", "capacity",
    "booking_type", "buffer_before", "buffer_after", "status",
)


def change_out(change: ScheduleChangeResult, result: Any = None) -> ScheduleChangeOut:
    return ScheduleChangeOut(
        applied=change.applied,
        affected_count=len(change.affected),
        affected_bookings=[
            AffectedBookingOut(booking_id=a.booking_id, code=a.code, message=a.message, booking=a.summary)
            for a in change.affected
        ],
        result=result,
    )


def _get(db, model, entity_id, name):
    obj = db.get(model, entity_id)
    if obj is None:
        raise NotFound(name)
    return obj


# ---------------------------------------------------------------- locations


@router.get("/locations", response_model=list[LocationOut], dependencies=[ManageLocations])
def list_locations(db: DB) -> list[Location]:
    return list(db.scalars(select(Location).order_by(Location.name)))


@router.post("/locations", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
def create_location(body: LocationIn, db: DB, actor: User = ManageLocations) -> Location:
    location = Location(**body.model_dump())
    db.add(location)
    db.flush()
    audit.record(db, actor_id=actor.id, action="LOCATION_CREATED", entity_type="location", entity_id=location.id, new=body.model_dump())
    db.commit()
    return location


@router.put("/locations/{location_id}", response_model=ScheduleChangeOut)
def update_location(
    location_id: uuid.UUID,
    body: LocationIn,
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageLocations,
) -> ScheduleChangeOut:
    location = _get(db, Location, location_id, "Location")
    old = audit.snapshot(location, "name", "address", "timezone", "status")
    resource_ids = list(db.scalars(select(Resource.id).where(Resource.location_id == location_id)))

    def mutate() -> Location:
        for key, value in body.model_dump().items():
            setattr(location, key, value)
        return location

    change = apply_schedule_change(
        db, actor, mutate=mutate, resource_ids=resource_ids, dry_run=dry_run,
        conflict_action=conflict_action, audit_action="LOCATION_UPDATED", entity_type="location",
        entity_id=location_id, audit_new={"old": old, "new": body.model_dump()},
    )
    return change_out(change, LocationOut.model_validate(db.get(Location, location_id)).model_dump(mode="json"))


@router.delete("/locations/{location_id}", response_model=ScheduleChangeOut)
def delete_location(
    location_id: uuid.UUID,
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageLocations,
) -> ScheduleChangeOut:
    location = _get(db, Location, location_id, "Location")
    has_resources = db.scalar(select(exists().where(Resource.location_id == location_id)))
    if not has_resources:
        if not dry_run:
            db.delete(location)
            audit.record(db, actor_id=actor.id, action="LOCATION_DELETED", entity_type="location", entity_id=location_id)
            db.commit()
        return ScheduleChangeOut(applied=not dry_run, affected_count=0, affected_bookings=[], result={"deleted": True})
    # Locations with resources are deactivated, never removed, to keep history.
    location.status = RecordStatus.INACTIVE
    db.commit()
    return ScheduleChangeOut(applied=True, affected_count=0, affected_bookings=[], result={"deleted": False, "deactivated": True})


# ---------------------------------------------------------------- resources


def _resource_detail(db, resource: Resource) -> ResourceDetailOut:
    rules = settings_service.get_rules(db)
    return ResourceDetailOut(
        **resource_out(resource, rules).model_dump(),
        services=[offering_out(link) for link in resource.service_links],
        availability=[
            AvailabilityRuleOut.model_validate(r)
            for r in sorted(resource.availability_rules, key=lambda r: (r.day_of_week, r.start_time))
        ],
    )


def _validate_location(db, location_id: uuid.UUID | None) -> None:
    if location_id is not None and db.get(Location, location_id) is None:
        raise AppError("INVALID_LOCATION", "Location not found", status_code=422)


@router.get("/resources", response_model=list[ResourceOut], dependencies=[ManageResources])
def list_resources(
    db: DB,
    q: str | None = Query(default=None, max_length=100),
    type: ResourceType | None = None,
    location_id: uuid.UUID | None = None,
    status_filter: RecordStatus | None = Query(default=None, alias="status"),
) -> list[ResourceOut]:
    rules = settings_service.get_rules(db)
    stmt = select(Resource)
    if q:
        stmt = stmt.where(or_(Resource.name.ilike(f"%{q}%"), Resource.description.ilike(f"%{q}%")))
    if type:
        stmt = stmt.where(Resource.type == type)
    if location_id:
        stmt = stmt.where(Resource.location_id == location_id)
    if status_filter:
        stmt = stmt.where(Resource.status == status_filter)
    return [resource_out(r, rules) for r in db.scalars(stmt.order_by(Resource.name)).unique()]


@router.get("/resources/{resource_id}", response_model=ResourceDetailOut, dependencies=[ManageResources])
def get_resource(resource_id: uuid.UUID, db: DB) -> ResourceDetailOut:
    return _resource_detail(db, _get(db, Resource, resource_id, "Resource"))


@router.post("/resources", response_model=ResourceDetailOut, status_code=status.HTTP_201_CREATED)
def create_resource(body: ResourceIn, db: DB, actor: User = ManageResources) -> ResourceDetailOut:
    _validate_location(db, body.location_id)
    data = body.model_dump()
    data["attributes"] = data.pop("metadata")
    resource = Resource(**data)
    db.add(resource)
    db.flush()
    audit.record(db, actor_id=actor.id, action="RESOURCE_CREATED", entity_type="resource", entity_id=resource.id, new=data)
    db.commit()
    db.refresh(resource)
    return _resource_detail(db, resource)


@router.put("/resources/{resource_id}", response_model=ScheduleChangeOut)
def update_resource(
    resource_id: uuid.UUID,
    body: ResourceIn,
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageResources,
) -> ScheduleChangeOut:
    resource = _get(db, Resource, resource_id, "Resource")
    _validate_location(db, body.location_id)
    old = audit.snapshot(resource, *RESOURCE_COLUMNS)
    data = body.model_dump()
    data["attributes"] = data.pop("metadata")

    def mutate() -> Resource:
        for key, value in data.items():
            setattr(resource, key, value)
        return resource

    change = apply_schedule_change(
        db, actor, mutate=mutate, resource_ids=[resource_id], dry_run=dry_run,
        conflict_action=conflict_action, audit_action="RESOURCE_UPDATED", entity_type="resource",
        entity_id=resource_id, audit_new={"old": old, "new": data},
    )
    fresh = db.get(Resource, resource_id)
    return change_out(change, _resource_detail(db, fresh).model_dump(mode="json"))


@router.delete("/resources/{resource_id}", response_model=ScheduleChangeOut)
def delete_resource(
    resource_id: uuid.UUID,
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageResources,
) -> ScheduleChangeOut:
    """Resources with any booking history are deactivated, not deleted.

    Future bookings on a deactivated resource are reported (dry_run) and then
    kept, marked as conflicts or cancelled according to ``conflict_action``.
    """
    resource = _get(db, Resource, resource_id, "Resource")
    has_history = db.scalar(
        select(
            exists().where(
                or_(
                    Booking.primary_resource_id == resource_id,
                    Booking.id.in_(select(BookingResource.booking_id).where(BookingResource.resource_id == resource_id)),
                )
            )
        )
    )
    if not has_history:
        if not dry_run:
            db.delete(resource)
            audit.record(db, actor_id=actor.id, action="RESOURCE_DELETED", entity_type="resource", entity_id=resource_id)
            db.commit()
        return ScheduleChangeOut(applied=not dry_run, affected_count=0, affected_bookings=[], result={"deleted": True})

    def mutate() -> Resource:
        resource.status = RecordStatus.INACTIVE
        return resource

    change = apply_schedule_change(
        db, actor, mutate=mutate, resource_ids=[resource_id], dry_run=dry_run,
        conflict_action=conflict_action, audit_action="RESOURCE_DEACTIVATED", entity_type="resource",
        entity_id=resource_id,
    )
    return change_out(change, {"deleted": False, "deactivated": change.applied})


@router.put("/resources/{resource_id}/services", response_model=list[OfferingOut])
def set_resource_services(
    resource_id: uuid.UUID, body: list[ResourceServiceIn], db: DB, actor: User = ManageResources
) -> list[OfferingOut]:
    """Replace which services this resource provides (existing bookings are kept)."""
    resource = _get(db, Resource, resource_id, "Resource")
    wanted = {item.service_id: item for item in body}
    if len(wanted) != len(body):
        raise AppError("DUPLICATE_SERVICE", "Each service may be listed once", status_code=422)
    for service_id in wanted:
        _get(db, Service, service_id, "Service")
    current = {link.service_id: link for link in resource.service_links}
    for service_id, link in current.items():
        if service_id not in wanted:
            resource.service_links.remove(link)
    for service_id, item in wanted.items():
        link = current.get(service_id)
        if link is None:
            link = ResourceService(resource_id=resource_id, service_id=service_id)
            resource.service_links.append(link)
        for key, value in item.model_dump(exclude={"service_id"}).items():
            setattr(link, key, value)
    audit.record(
        db, actor_id=actor.id, action="RESOURCE_SERVICES_CHANGED", entity_type="resource", entity_id=resource_id,
        old={"services": [str(s) for s in current]}, new={"services": [item.model_dump(mode="json") for item in body]},
    )
    db.commit()
    db.refresh(resource)
    return [offering_out(link) for link in resource.service_links]


@router.get("/resources/{resource_id}/availability", response_model=list[AvailabilityRuleOut], dependencies=[ManageSchedules])
def get_resource_availability(resource_id: uuid.UUID, db: DB) -> list[ResourceAvailability]:
    resource = _get(db, Resource, resource_id, "Resource")
    return sorted(resource.availability_rules, key=lambda r: (r.day_of_week, r.start_time))


@router.put("/resources/{resource_id}/availability", response_model=ScheduleChangeOut)
def set_resource_availability(
    resource_id: uuid.UUID,
    body: list[AvailabilityRuleIn],
    db: DB,
    dry_run: bool = False,
    conflict_action: ConflictAction = "mark_conflicted",
    actor: User = ManageSchedules,
) -> ScheduleChangeOut:
    """Replace the weekly schedule. An empty list means "no restriction"
    (the resource is then available whenever the business is open)."""
    resource = _get(db, Resource, resource_id, "Resource")
    old = [AvailabilityRuleOut.model_validate(r).model_dump(mode="json") for r in resource.availability_rules]

    def mutate() -> None:
        db.execute(delete(ResourceAvailability).where(ResourceAvailability.resource_id == resource_id))
        for rule in body:
            db.add(ResourceAvailability(resource_id=resource_id, **rule.model_dump()))
        db.flush()
        db.expire(resource, ["availability_rules"])

    change = apply_schedule_change(
        db, actor, mutate=mutate, resource_ids=[resource_id], dry_run=dry_run,
        conflict_action=conflict_action, audit_action="SCHEDULE_CHANGED", entity_type="resource",
        entity_id=resource_id, audit_new={"old": old, "new": [r.model_dump(mode="json") for r in body]},
    )
    fresh = db.get(Resource, resource_id)
    db.expire(fresh, ["availability_rules"])
    rules = [AvailabilityRuleOut.model_validate(r).model_dump(mode="json") for r in fresh.availability_rules]
    return change_out(change, rules)


# ---------------------------------------------------------------- services


def _service_detail(service: Service) -> ServiceDetailOut:
    return ServiceDetailOut(
        **ServiceOut.model_validate(service).model_dump(),
        resources=[offering_out(link) for link in service.resource_links],
    )


@router.get("/services", response_model=list[ServiceOut], dependencies=[ManageServices])
def list_services(db: DB, status_filter: RecordStatus | None = Query(default=None, alias="status")) -> list[Service]:
    stmt = select(Service)
    if status_filter:
        stmt = stmt.where(Service.status == status_filter)
    return list(db.scalars(stmt.order_by(Service.name)))


@router.get("/services/{service_id}", response_model=ServiceDetailOut, dependencies=[ManageServices])
def get_service(service_id: uuid.UUID, db: DB) -> ServiceDetailOut:
    return _service_detail(_get(db, Service, service_id, "Service"))


@router.post("/services", response_model=ServiceDetailOut, status_code=status.HTTP_201_CREATED)
def create_service(body: ServiceIn, db: DB, actor: User = ManageServices) -> ServiceDetailOut:
    service = Service(**body.model_dump())
    db.add(service)
    db.flush()
    audit.record(db, actor_id=actor.id, action="SERVICE_CREATED", entity_type="service", entity_id=service.id, new=body.model_dump())
    db.commit()
    db.refresh(service)
    return _service_detail(service)


@router.put("/services/{service_id}", response_model=ServiceDetailOut)
def update_service(service_id: uuid.UUID, body: ServiceIn, db: DB, actor: User = ManageServices) -> ServiceDetailOut:
    """Existing bookings keep the times they were made with; a new duration
    or buffer only applies to bookings made afterwards."""
    service = _get(db, Service, service_id, "Service")
    old = audit.snapshot(service, *SERVICE_COLUMNS)
    for key, value in body.model_dump().items():
        setattr(service, key, value)
    audit.record(db, actor_id=actor.id, action="SERVICE_UPDATED", entity_type="service", entity_id=service_id, old=old, new=body.model_dump())
    db.commit()
    db.refresh(service)
    return _service_detail(service)


@router.delete("/services/{service_id}")
def delete_service(service_id: uuid.UUID, db: DB, actor: User = ManageServices) -> dict:
    service = _get(db, Service, service_id, "Service")
    if db.scalar(select(exists().where(Booking.service_id == service_id))):
        service.status = RecordStatus.INACTIVE
        audit.record(db, actor_id=actor.id, action="SERVICE_DEACTIVATED", entity_type="service", entity_id=service_id)
        db.commit()
        return {"deleted": False, "deactivated": True}
    db.delete(service)
    audit.record(db, actor_id=actor.id, action="SERVICE_DELETED", entity_type="service", entity_id=service_id)
    db.commit()
    return {"deleted": True, "deactivated": False}
