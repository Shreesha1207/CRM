"""The assistant's tools: the only way its model can see or change anything.

Each tool is a fixed function that runs our own code as the signed-in customer
(through the same catalogue queries and booking engine the website uses) and
returns a small JSON result. The model never sees SQL, tables or other customers.
"""

import difflib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes import catalog
from app.api.routes.bookings import create_with_auto_assign, scope_filter
from app.api.serializers import bookings_out
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.timeutils import ensure_aware, utcnow
from app.db.session import SessionLocal
from app.models import Booking, ChatProposal, Resource, Service, User
from app.models.enums import BookingType, ChatProposalKind, ChatProposalStatus, RecordStatus
from app.schemas import BookingCreateIn
from app.services import booking_mail, settings_service
from app.services.availability import resource_timezone
from app.services.booking_engine import BookingEngine, ChatHold
from app.services.chat import proposals
from app.services.notifications import _clock

log = logging.getLogger("chat.tools")

MAX_TIMES_PER_DAY = 30
MAX_BOOKINGS = 20


@dataclass
class ToolContext:
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    turn_id: uuid.UUID
    proposal_ids: list[uuid.UUID] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def _money(value) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _closest(query: str, names: list[str]) -> list[str]:
    lowered = {n.lower(): n for n in names}
    words = [n for n in names if any(w in n.lower() for w in query.lower().split() if len(w) > 2)]
    close = [lowered[m] for m in difflib.get_close_matches(query.lower(), list(lowered), n=5, cutoff=0.4)]
    return list(dict.fromkeys(words + close))[:5]


def _active_links(service: Service) -> list:
    return [
        link
        for link in service.resource_links
        if link.status == RecordStatus.ACTIVE and link.resource.status == RecordStatus.ACTIVE
    ]


# ---------------------------------------------------------------- catalogue


class ListServicesIn(_In):
    query: str | None = Field(default=None, max_length=100)
    location_id: uuid.UUID | None = None


def list_services(db: Session, user: User, ctx: ToolContext, args: ListServicesIn) -> dict[str, Any]:
    services = catalog.list_services(db, q=args.query, resource_id=None, location_id=args.location_id, max_price=None)
    out = []
    for s in services[:20]:
        flexible = s.booking_type == BookingType.INDIVIDUAL and s.max_duration_minutes
        out.append(
            {
                "id": str(s.id),
                "name": s.name,
                "description": (s.description or "")[:160] or None,
                "length_minutes": s.duration_minutes,
                # The customer may book longer, in multiples of length_minutes up to this.
                "max_length_minutes": s.max_duration_minutes if flexible else None,
                "rate_per_hour": _money(s.price),
                "group_session": s.booking_type == BookingType.CAPACITY,
                "resources": [{"id": str(l.resource.id), "name": l.resource.name} for l in _active_links(s)][:10],
            }
        )
    result: dict[str, Any] = {"services": out}
    if args.query and not out:
        names = [s.name for s in catalog.list_services(db, q=None, resource_id=None, location_id=None, max_price=None)]
        result["closest_names"] = _closest(args.query, names)
    return result


class ListResourcesIn(_In):
    query: str | None = Field(default=None, max_length=100)
    service_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None


def list_resources(db: Session, user: User, ctx: ToolContext, args: ListResourcesIn) -> dict[str, Any]:
    rules = settings_service.get_rules(db)
    stmt = select(Resource).where(Resource.status == RecordStatus.ACTIVE)
    if args.query:
        stmt = stmt.where(func.lower(Resource.name).contains(args.query.lower()))
    if args.service_id:
        stmt = stmt.where(Resource.service_links.any(service_id=args.service_id, status=RecordStatus.ACTIVE))
    if args.location_id:
        stmt = stmt.where(Resource.location_id == args.location_id)
    resources = db.scalars(stmt.order_by(Resource.name).limit(20)).unique().all()
    out = [
        {
            "id": str(r.id),
            "name": r.name,
            "type": r.type.value.lower(),
            "location": r.location.name if r.location else None,
            "timezone": resource_timezone(r, rules).key,
            "services": [
                {"id": str(l.service.id), "name": l.service.name}
                for l in r.service_links
                if l.status == RecordStatus.ACTIVE and l.service.status == RecordStatus.ACTIVE
            ][:10],
        }
        for r in resources
    ]
    result: dict[str, Any] = {"resources": out}
    if args.query and not out:
        names = list(db.scalars(select(Resource.name).where(Resource.status == RecordStatus.ACTIVE)))
        result["closest_names"] = _closest(args.query, names)
    return result


class ListLocationsIn(_In):
    pass


def list_locations(db: Session, user: User, ctx: ToolContext, args: ListLocationsIn) -> dict[str, Any]:
    return {
        "locations": [
            {"id": str(l.id), "name": l.name, "address": l.address, "timezone": l.timezone}
            for l in catalog.list_locations(db)
        ]
    }


class FindSlotsIn(_In):
    service_id: uuid.UUID
    date: date
    days: int = Field(default=1, ge=1, le=7)
    resource_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    quantity: int = Field(default=1, ge=1, le=100)
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    rescheduling_booking_id: uuid.UUID | None = None


def find_available_slots(db: Session, user: User, ctx: ToolContext, args: FindSlotsIn) -> dict[str, Any]:
    moving = BookingEngine(db, user).get_visible_booking(args.rescheduling_booking_id) if args.rescheduling_booking_id else None
    days, timezone, length, rate = [], None, None, None
    for offset in range(args.days):
        day = args.date + timedelta(days=offset)
        result = catalog.availability(
            db,
            service_id=args.service_id,
            date=day,
            moving=moving,
            resource_id=args.resource_id,
            location_id=args.location_id,
            quantity=args.quantity,
            duration_minutes=args.duration_minutes,
        )
        names = {r.resource_id: r.resource_name for r in result.resources}
        if result.resources:
            timezone = timezone or result.resources[0].timezone
            length = length or result.resources[0].duration_minutes
            rate = rate or _money(result.resources[0].price)
        times = [
            {
                "start": slot.start.strftime("%Y-%m-%dT%H:%M"),
                "label": _clock(slot.start),
                "resources": [{"id": str(rid), "name": names.get(rid)} for rid in slot.resource_ids][:3],
            }
            for slot in result.slots
            if slot.available
        ]
        days.append(
            {
                "date": day.isoformat(),
                "label": f"{day:%a %d %b}",
                "times": times[:MAX_TIMES_PER_DAY],
                "more_times": max(0, len(times) - MAX_TIMES_PER_DAY),
            }
        )
    return {"timezone": timezone, "length_minutes": length, "rate_per_hour": rate, "days": days}


class AlternativesIn(_In):
    service_id: uuid.UUID
    resource_id: uuid.UUID
    start: datetime
    quantity: int = Field(default=1, ge=1, le=100)
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)


def suggest_alternatives(db: Session, user: User, ctx: ToolContext, args: AlternativesIn) -> dict[str, Any]:
    options = catalog.alternatives(
        db,
        service_id=args.service_id,
        resource_id=args.resource_id,
        start=args.start,
        quantity=args.quantity,
        duration_minutes=args.duration_minutes,
    )
    return {
        "alternatives": [
            {
                "start": o.start.strftime("%Y-%m-%dT%H:%M"),
                "label": f"{o.start:%a %d %b}, {_clock(o.start)}",
                "timezone": o.timezone,
                "resource_id": str(o.resource_id),
                "resource": o.resource_name,
            }
            for o in options[:10]
        ]
    }


# ---------------------------------------------------------------- the customer's bookings


def _booking_json(db: Session, booking: Booking, user: User, flags) -> dict[str, Any]:
    facts = booking_mail.facts(db, booking, user.id)
    return {
        "id": str(booking.id),
        "service": facts.service,
        "resource": facts.resource,
        "date": facts.when.date,
        "time": facts.when.time,
        "timezone": facts.when.zone,
        "place": facts.place,
        "places": booking.quantity,
        "status": booking.status.value.lower(),
        "price": _money(booking.price),
        "can_cancel": flags.can_cancel,
        "can_reschedule": flags.can_reschedule,
        "waiting_for_confirmation_in_chat": flags.held_in_chat,
    }


class ListBookingsIn(_In):
    scope: Literal["upcoming", "past", "cancelled", "waitlisted", "all"] = "upcoming"
    limit: int = Field(default=10, ge=1, le=MAX_BOOKINGS)


def list_my_bookings(db: Session, user: User, ctx: ToolContext, args: ListBookingsIn) -> dict[str, Any]:
    now = utcnow()
    stmt = scope_filter(select(Booking).where(Booking.user_id == user.id), args.scope, now)
    bookings = db.scalars(stmt.limit(args.limit)).unique().all()
    rules = settings_service.get_rules(db)
    flags = bookings_out(db, bookings, user, rules, now)
    return {"bookings": [_booking_json(db, b, user, f) for b, f in zip(bookings, flags, strict=True)]}


class GetBookingIn(_In):
    booking_id: uuid.UUID


def _mine(db: Session, user: User, booking_id: uuid.UUID) -> Booking:
    booking = BookingEngine(db, user).get_visible_booking(booking_id)
    if booking.user_id != user.id:
        raise AppError("NOT_FOUND", "Booking not found")
    return booking


def get_booking(db: Session, user: User, ctx: ToolContext, args: GetBookingIn) -> dict[str, Any]:
    booking = _mine(db, user, args.booking_id)
    flags = bookings_out(db, [booking], user, settings_service.get_rules(db), utcnow())[0]
    return _booking_json(db, booking, user, flags)


# ---------------------------------------------------------------- booking and changes


class HoldBookingIn(_In):
    service_id: uuid.UUID
    start: datetime
    resource_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    quantity: int = Field(default=1, ge=1, le=100)


def hold_booking(db: Session, user: User, ctx: ToolContext, args: HoldBookingIn) -> dict[str, Any]:
    settings = get_settings()
    now = utcnow()
    open_holds = db.scalar(
        select(func.count())
        .select_from(ChatProposal)
        .where(
            ChatProposal.user_id == user.id,
            ChatProposal.kind == ChatProposalKind.HOLD,
            ChatProposal.status == ChatProposalStatus.PENDING,
            ChatProposal.expires_at > now,
        )
    )
    if open_holds >= settings.chat_max_open_holds:
        return _error(
            "HOLD_LIMIT",
            f"The customer already has {open_holds} held bookings waiting. Ask them to confirm or delete those first.",
        )
    body = BookingCreateIn(
        service_id=args.service_id,
        resource_id=args.resource_id,
        start=args.start,
        quantity=args.quantity,
        duration_minutes=args.duration_minutes,
    )
    engine = BookingEngine(db, user)
    hold = ChatHold(ctx.conversation_id, ctx.turn_id, now + timedelta(minutes=settings.chat_hold_minutes))
    booking = create_with_auto_assign(engine, body, user_id=user.id, chat_hold=hold)
    proposal = engine.open_chat_hold(booking.id)
    ctx.proposal_ids.append(proposal.id)
    card = proposals.card(db, proposal, user, now)
    return {
        "held": True,
        "booking": {k: card[k] for k in ("service", "resource", "when", "place", "price", "quantity")},
        "hold_minutes": settings.chat_hold_minutes,
        "next_step": "Tell the customer the time is held for them, and that they can press Confirm on the card to book it, or Delete.",
    }


class RescheduleIn(_In):
    booking_id: uuid.UUID
    new_start: datetime
    resource_id: uuid.UUID | None = None


def _new_card(db: Session, user: User, ctx: ToolContext, kind: ChatProposalKind, booking: Booking, payload: dict) -> ChatProposal:
    now = utcnow()
    proposal = ChatProposal(
        conversation_id=ctx.conversation_id,
        user_id=user.id,
        turn_id=ctx.turn_id,
        kind=kind,
        status=ChatProposalStatus.PENDING,
        booking_id=booking.id,
        payload=payload,
        expires_at=now + timedelta(minutes=get_settings().chat_hold_minutes),
        created_at=now,
        updated_at=now,
    )
    db.add(proposal)
    db.commit()
    ctx.proposal_ids.append(proposal.id)
    return proposal


def propose_reschedule(db: Session, user: User, ctx: ToolContext, args: RescheduleIn) -> dict[str, Any]:
    booking = _mine(db, user, args.booking_id)
    rules = settings_service.get_rules(db)
    flags = bookings_out(db, [booking], user, rules, utcnow())[0]
    if flags.held_in_chat:
        return _error("HELD_IN_CHAT", "This booking is still waiting for the customer to confirm or delete it. Delete it and hold the new time instead.")
    if not flags.can_reschedule:
        return _error("CANNOT_RESCHEDULE", "This booking can no longer be moved online. Suggest contacting the business.")
    target = db.get(Resource, args.resource_id or booking.primary_resource_id)
    if target is None or target.status != RecordStatus.ACTIVE:
        return _error("NOT_FOUND", "Resource not found")
    zone = resource_timezone(target, rules)
    start = ensure_aware(args.new_start, zone)
    result = catalog.availability(
        db,
        service_id=booking.service_id,
        date=start.astimezone(zone).date(),
        moving=booking,
        resource_id=target.id,
        location_id=None,
        quantity=booking.quantity,
        duration_minutes=None,
    )
    free = any(s.available and s.start == start for r in result.resources for s in r.slots)
    if not free:
        return _error(
            "NOT_AVAILABLE",
            "That time is not free. Use find_available_slots with rescheduling_booking_id to find free times.",
        )
    proposal = _new_card(db, user, ctx, ChatProposalKind.RESCHEDULE, booking, {"new_start": start.isoformat(), "resource_id": str(target.id)})
    card = proposals.card(db, proposal, user)
    return {
        "proposed": True,
        "change": {"from": card["was"], "to": card["when"], "service": card["service"], "resource": card["resource"]},
        "next_step": "Tell the customer to press Confirm on the card to move it, or Decline to keep it as it is.",
    }


class CancelIn(_In):
    booking_id: uuid.UUID


def propose_cancellation(db: Session, user: User, ctx: ToolContext, args: CancelIn) -> dict[str, Any]:
    booking = _mine(db, user, args.booking_id)
    flags = bookings_out(db, [booking], user, settings_service.get_rules(db), utcnow())[0]
    if flags.held_in_chat:
        return _error("HELD_IN_CHAT", "This booking was never confirmed: the customer can press Delete on its card.")
    if not flags.can_cancel:
        return _error("CANNOT_CANCEL", "This booking can no longer be cancelled online. Suggest contacting the business.")
    proposal = _new_card(db, user, ctx, ChatProposalKind.CANCEL, booking, {})
    card = proposals.card(db, proposal, user)
    return {
        "proposed": True,
        "booking": {"service": card["service"], "resource": card["resource"], "when": card["when"]},
        "next_step": "Tell the customer to press Confirm on the card to cancel it, or Decline to keep it.",
    }


class ChoicesIn(_In):
    question: str = Field(max_length=200)
    options: list[str] = Field(min_length=2, max_length=6)


def offer_choices(db: Session, user: User, ctx: ToolContext, args: ChoicesIn) -> dict[str, Any]:
    options = [o.strip()[:60] for o in args.options if o.strip()]
    ctx.choices = list(dict.fromkeys(options))
    return {
        "shown": True,
        "next_step": "The options are shown as buttons under your reply. Ask the question in your reply and stop there.",
    }


# ---------------------------------------------------------------- definitions


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    model: type[_In]
    handler: Callable[[Session, User, ToolContext, Any], dict[str, Any]]
    status: str  # shown to the customer while it runs

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "additionalProperties": False, **self.schema},
        }


_UUID = {"type": "string", "format": "uuid"}
_START = {"type": "string", "description": "Wall-clock time at the resource's location, YYYY-MM-DDTHH:MM, e.g. 2026-10-20T10:00"}

TOOLS = [
    Tool(
        "list_services",
        "List the services the business offers, with their length, hourly rate and who or what provides them. "
        "Use query to search by name. When nothing matches, closest_names lists similar names.",
        {"properties": {"query": {"type": "string"}, "location_id": _UUID}},
        ListServicesIn,
        list_services,
        "Looking up services…",
    ),
    Tool(
        "list_resources",
        "List the people, rooms or other resources that can be booked, with the services each offers and its time zone. "
        "Use query to search by name (e.g. a first name). When nothing matches, closest_names lists similar names.",
        {"properties": {"query": {"type": "string"}, "service_id": _UUID, "location_id": _UUID}},
        ListResourcesIn,
        list_resources,
        "Looking up who's available…",
    ),
    Tool(
        "list_locations",
        "List the business's locations with their address and time zone.",
        {"properties": {}},
        ListLocationsIn,
        list_locations,
        "Looking up locations…",
    ),
    Tool(
        "find_available_slots",
        "Free start times for a service from a date, for up to 7 days. Times are wall-clock times in the given time zone. "
        "Pass duration_minutes for a longer booking of a flexible service, quantity for places in a group session, and "
        "rescheduling_booking_id when looking for a new time for one of the customer's existing bookings.",
        {
            "properties": {
                "service_id": _UUID,
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "days": {"type": "integer", "minimum": 1, "maximum": 7},
                "resource_id": _UUID,
                "location_id": _UUID,
                "quantity": {"type": "integer", "minimum": 1},
                "duration_minutes": {"type": "integer", "minimum": 1},
                "rescheduling_booking_id": _UUID,
            },
            "required": ["service_id", "date"],
        },
        FindSlotsIn,
        find_available_slots,
        "Checking availability…",
    ),
    Tool(
        "suggest_alternatives",
        "Nearby free times, with this or another resource, when a requested time is not free.",
        {
            "properties": {
                "service_id": _UUID,
                "resource_id": _UUID,
                "start": _START,
                "quantity": {"type": "integer", "minimum": 1},
                "duration_minutes": {"type": "integer", "minimum": 1},
            },
            "required": ["service_id", "resource_id", "start"],
        },
        AlternativesIn,
        suggest_alternatives,
        "Looking for other times…",
    ),
    Tool(
        "list_my_bookings",
        "The signed-in customer's own bookings (upcoming by default), with whether each can still be cancelled or moved.",
        {
            "properties": {
                "scope": {"type": "string", "enum": ["upcoming", "past", "cancelled", "waitlisted", "all"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_BOOKINGS},
            }
        },
        ListBookingsIn,
        list_my_bookings,
        "Looking at your bookings…",
    ),
    Tool(
        "get_booking",
        "One of the customer's own bookings.",
        {"properties": {"booking_id": _UUID}, "required": ["booking_id"]},
        GetBookingIn,
        get_booking,
        "Looking at your booking…",
    ),
    Tool(
        "hold_booking",
        "Book a time for the customer straight away and hold it for them: the customer sees a card with the details and "
        "presses Confirm to book it or Delete to release it. Omit resource_id to take any suitable free resource. "
        "Use duration_minutes only for a longer booking of a flexible service, quantity for places in a group session. "
        "On a rule problem it returns error and message: explain it and offer other times.",
        {
            "properties": {
                "service_id": _UUID,
                "start": _START,
                "resource_id": _UUID,
                "duration_minutes": {"type": "integer", "minimum": 1},
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["service_id", "start"],
        },
        HoldBookingIn,
        hold_booking,
        "Holding your booking…",
    ),
    Tool(
        "propose_reschedule",
        "Offer to move one of the customer's bookings to a new time (and optionally another resource). Nothing changes "
        "until the customer presses Confirm on the card.",
        {
            "properties": {"booking_id": _UUID, "new_start": _START, "resource_id": _UUID},
            "required": ["booking_id", "new_start"],
        },
        RescheduleIn,
        propose_reschedule,
        "Preparing the change…",
    ),
    Tool(
        "propose_cancellation",
        "Offer to cancel one of the customer's bookings. Nothing changes until the customer presses Confirm on the card.",
        {"properties": {"booking_id": _UUID}, "required": ["booking_id"]},
        CancelIn,
        propose_cancellation,
        "Preparing the cancellation…",
    ),
    Tool(
        "offer_choices",
        "Show 2 to 6 short options as buttons under your reply (free times, matching services or people, yes/no). "
        "A click sends the option back as the customer's answer.",
        {
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
            },
            "required": ["question", "options"],
        },
        ChoicesIn,
        offer_choices,
        "",
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def definitions() -> list[dict[str, Any]]:
    return [t.definition() for t in TOOLS]


def run(name: str, raw_input: Any, ctx: ToolContext) -> tuple[str, bool]:
    """Run one tool call. Returns the JSON result for the model and whether it is an error."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        result = _error("UNKNOWN_TOOL", f"There is no tool called {name}")
    else:
        try:
            args = tool.model.model_validate(raw_input if isinstance(raw_input, dict) else {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()[:5])
            result = _error("INVALID_INPUT", problems)
        else:
            with SessionLocal() as db:
                user = db.get(User, ctx.user_id)
                try:
                    result = tool.handler(db, user, ctx, args)
                except AppError as exc:
                    db.rollback()
                    result = _error(exc.code, exc.message)
                except Exception:
                    db.rollback()
                    log.exception("Tool %s failed", name)
                    result = _error("INTERNAL", "Something went wrong; try again or suggest the booking page.")
    return json.dumps(result, separators=(",", ":"), default=str), "error" in result
