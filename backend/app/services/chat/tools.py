"""The assistant's tools: the only way its model can see or change anything.

Each tool is a fixed function that runs our own code as the signed-in customer
(through the same catalogue queries and booking engine the website uses) and
returns a small JSON result. The model never sees SQL, tables or other customers.
"""

import difflib
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
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


# What small models write for a field they don't know.
_PLACEHOLDERS = {"", "?", "-", "null", "none", "n/a", "na", "unknown", "undefined", "tbd"}


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _leave_out_placeholders(cls, data: Any) -> Any:
        """"?" or "null" for an optional field means "not given"; for a required one it
        becomes a plain "field required" the model can act on."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip().lower() in _PLACEHOLDERS)}
        return data


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


# Above this the catalogue is left out of the prompt and found with the tools instead.
MAX_CATALOGUE_SERVICES = 40
MAX_CATALOGUE_RESOURCES = 60


def catalogue(db: Session) -> dict[str, Any] | None:
    """Everything that can be booked, for the prompt: the only names that exist."""
    services = catalog.list_services(db, q=None, resource_id=None, location_id=None, max_price=None)
    resources = db.scalars(select(Resource).where(Resource.status == RecordStatus.ACTIVE).order_by(Resource.name)).unique().all()
    if len(services) > MAX_CATALOGUE_SERVICES or len(resources) > MAX_CATALOGUE_RESOURCES:
        return None
    rules = settings_service.get_rules(db)
    return {
        "services": [
            {
                "name": s.name,
                "description": (s.description or "")[:120] or None,
                "length_minutes": s.duration_minutes,
                "max_length_minutes": s.max_duration_minutes if s.booking_type == BookingType.INDIVIDUAL else None,
                "rate_per_hour": _money(s.price),
                "group_session": s.booking_type == BookingType.CAPACITY,
                "provided_by": [l.resource.name for l in _active_links(s)],
            }
            for s in services
        ],
        "resources": [
            {
                "name": r.name,
                "type": r.type.value.lower(),
                "location": r.location.name if r.location else None,
                "timezone": resource_timezone(r, rules).key,
            }
            for r in resources
        ],
        "locations": [{"name": l.name, "address": l.address} for l in catalog.list_locations(db)],
    }


class ListServicesIn(_In):
    query: str | None = Field(default=None, max_length=100)
    location_id: uuid.UUID | None = None
    # With a date, every service also says which start times are free that day.
    on_date: date | None = None


def list_services(db: Session, user: User, ctx: ToolContext, args: ListServicesIn) -> dict[str, Any]:
    services = catalog.list_services(db, q=args.query, resource_id=None, location_id=args.location_id, max_price=None)
    out = []
    for s in services[:20]:
        flexible = s.booking_type == BookingType.INDIVIDUAL and s.max_duration_minutes
        entry = {
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
        if args.on_date is not None:
            free = catalog.availability(
                db,
                service_id=s.id,
                date=args.on_date,
                moving=None,
                resource_id=None,
                location_id=args.location_id,
                quantity=1,
                duration_minutes=None,
            )
            times = [slot for slot in free.slots if slot.available]
            entry["free_times_on_date"] = {"date": args.on_date.isoformat(), "count": len(times), "first": [_clock(t.start) for t in times[:6]]}
        out.append(entry)
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
    # Narrow it down to the booking the customer means, in each booking's own time zone.
    on_date: date | None = None
    around_time: time | None = None
    query: str | None = Field(default=None, max_length=100)


# Words that say nothing about which booking is meant.
_VAGUE = {"my", "the", "a", "an", "with", "booking", "bookings", "appointment", "appointments", "session", "sessions", "meeting", "slot", "one"}


def _names_match(query: str, names: list[str]) -> bool:
    """Whether "yoga", "Sam", "personal trainng" or "the coach" names this booking."""
    hay = " ".join(names).lower()
    if query.lower().strip() in hay:
        return True
    words = [w for w in re.findall(r"\w+", query.lower()) if w not in _VAGUE and len(w) > 1]
    if not words:
        return True  # nothing specific to match on
    tokens = re.findall(r"\w+", hay)
    return all(any(w in t or difflib.SequenceMatcher(None, w, t).ratio() >= 0.8 for t in tokens) for w in words)


def list_my_bookings(db: Session, user: User, ctx: ToolContext, args: ListBookingsIn) -> dict[str, Any]:
    now = utcnow()
    rules = settings_service.get_rules(db)
    stmt = scope_filter(select(Booking).where(Booking.user_id == user.id), args.scope, now)
    filtering = args.on_date is not None or args.around_time is not None or bool(args.query)
    bookings = db.scalars(stmt.limit(200 if filtering else args.limit)).unique().all()

    def matches(b: Booking) -> bool:
        start = b.start_datetime.astimezone(resource_timezone(b.primary_resource, rules))
        if args.on_date is not None and start.date() != args.on_date:
            return False
        if args.around_time is not None:
            wanted = args.around_time.hour * 60 + args.around_time.minute
            if abs(start.hour * 60 + start.minute - wanted) > 60:
                return False
        names = [b.service.name, b.primary_resource.name, b.service.description or "", b.primary_resource.description or ""]
        return not args.query or _names_match(args.query, names)

    found = [b for b in bookings if matches(b)][: args.limit] if filtering else list(bookings)
    flags = bookings_out(db, found, user, rules, now)
    result: dict[str, Any] = {
        "bookings": [_booking_json(db, b, user, f) for b, f in zip(found, flags, strict=True)],
        "match_count": len(found),
    }
    if filtering and not found:
        others = bookings[:5]
        others_flags = bookings_out(db, others, user, rules, now)
        result["other_upcoming" if args.scope == "upcoming" else "other_bookings"] = [
            _booking_json(db, b, user, f) for b, f in zip(others, others_flags, strict=True)
        ]
    return result


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
    # A booking still waiting for Confirm that this one changes: it is released as the new one is held.
    replaces_booking_id: uuid.UUID | None = None


def _minutes(booking: Booking) -> int:
    return int((booking.end_datetime - booking.start_datetime).total_seconds() // 60)


def _replace_hold(db: Session, user: User, args: HoldBookingIn, engine: BookingEngine) -> dict[str, Any] | None:
    """Release the held booking this one replaces, once the new time is known to be free.
    Returns an error result instead when it can't be done, leaving the old hold as it was."""
    old = _mine(db, user, args.replaces_booking_id)
    if engine.open_chat_hold(old.id) is None:
        return _error("NOT_A_HOLD", "Only a booking still waiting for Confirm can be replaced. To move a booked one, use propose_reschedule.")
    rules = settings_service.get_rules(db)
    target = db.get(Resource, args.resource_id) if args.resource_id else old.primary_resource
    start = ensure_aware(args.start, resource_timezone(target, rules))
    free = catalog.availability(
        db,
        service_id=args.service_id,
        date=start.astimezone(resource_timezone(target, rules)).date(),
        moving=old,
        resource_id=args.resource_id,
        location_id=None,
        quantity=args.quantity,
        duration_minutes=args.duration_minutes,
    )
    if not any(slot.available and slot.start == start for r in free.resources for slot in r.slots):
        return _error("NOT_AVAILABLE", "That time is not free, so the earlier held booking was left as it is. Use find_available_slots to find free times.")
    engine.release_chat_hold(old.id, reason="Replaced in the chat")
    return None


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
    if open_holds >= settings.chat_max_open_holds and args.replaces_booking_id is None:
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
    if args.replaces_booking_id is not None and (failure := _replace_hold(db, user, args, engine)) is not None:
        return failure
    hold = ChatHold(ctx.conversation_id, ctx.turn_id, now + timedelta(minutes=settings.chat_hold_minutes))
    booking = create_with_auto_assign(engine, body, user_id=user.id, chat_hold=hold)
    proposal = engine.open_chat_hold(booking.id)
    ctx.proposal_ids.append(proposal.id)
    card = proposals.card(db, proposal, user, now)
    return {
        "held": True,
        "booking_id": str(booking.id),
        "length_minutes": _minutes(booking),
        "booking": {k: card[k] for k in ("service", "resource", "when", "place", "price", "quantity")},
        "hold_minutes": settings.chat_hold_minutes,
        "next_step": "Tell the customer the time is held for them, and that they can press Confirm on the card to book it, or Delete.",
    }


class RescheduleIn(_In):
    booking_id: uuid.UUID
    new_start: datetime
    resource_id: uuid.UUID | None = None
    # Only to say what the customer asked for: a move keeps the booking's length.
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)


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
    length = _minutes(booking)
    if args.duration_minutes is not None and args.duration_minutes != length:
        return _error(
            "LENGTH_CANNOT_CHANGE",
            f"A move keeps the booking's length ({length} minutes). For {args.duration_minutes} minutes, tell the customer that, and offer to "
            "hold a new session of that length (hold_booking) and cancel this one (propose_cancellation); each shows a card to confirm.",
        )
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
        "length_minutes": length,
        "change": {"from": card["was"], "to": card["when"], "service": card["service"], "resource": card["resource"]},
        "next_step": f"Describe the new time exactly as in change.to (the length stays {length} minutes). "
        "Tell the customer to press Confirm on the card to move it, or Decline to keep it as it is.",
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


# ---------------------------------------------------------------- the tools the model uses
#
# The model passes names and wall-clock times as the customer said them. These resolve
# them against the catalogue and the customer's bookings, run the handlers above, and set
# the buttons themselves when the customer has to choose, so a booking or a change takes
# one tool call instead of a chain the model can get wrong.

# Errors after which other times would not help.
_NO_OTHER_TIMES = {"HOLD_LIMIT", "NOT_A_HOLD", "INVALID_DURATION", "INVALID_QUANTITY", "NOT_FOUND", "INVALID_INPUT",
                   "SERVICE_INACTIVE", "RESOURCE_INACTIVE", "USER_INACTIVE", "ALREADY_BOOKED", "HELD_IN_CHAT",
                   "CANNOT_RESCHEDULE"}  # fmt: skip


def _pick(query: str, items: list[Any], names: Callable[[Any], list[str]]) -> tuple[list[Any], bool]:
    """The items a name refers to, and whether the match is sure: the exact name, or every
    word of it in a name or description (typos allowed). Otherwise the closest names,
    which the customer has to confirm."""
    wanted = query.strip().lower()
    exact = [i for i in items if names(i)[0].lower() == wanted]
    if exact:
        return exact, True
    matched = [i for i in items if _names_match(query, names(i))]
    if matched:
        return matched, True
    close = _closest(query, [names(i)[0] for i in items])
    return [i for i in items if names(i)[0] in close], False


def _ask(ctx: ToolContext, what: str, said: str, options: list[str], *, sure: bool, note: str = "") -> dict[str, Any]:
    """A question for the customer, with the options already shown as buttons."""
    ctx.choices = list(dict.fromkeys(options))[:6]
    if sure:
        why = f"Several {what}s match {said!r}."
    elif options:
        why = f"No {what} is called {said!r}; the closest names are the options."
    else:
        why = f"No {what} is called {said!r}."
    return {
        "needs": what,
        "options": ctx.choices,
        "next_step": f"{note}{why} Ask which {what} they mean in one short sentence; the options are shown as buttons. "
        "Never say that one of the options does not exist.",
    }


def _service(db: Session, ctx: ToolContext, name: str) -> tuple[Service | None, dict[str, Any] | None]:
    services = catalog.list_services(db, q=None, resource_id=None, location_id=None, max_price=None)
    found, sure = _pick(name, services, lambda s: [s.name, s.description or ""])
    if len(found) == 1 and sure:
        return found[0], None
    return None, _ask(ctx, "service", name, [s.name for s in found or services], sure=sure and bool(found))


def _person(db: Session, ctx: ToolContext, service: Service, name: str) -> tuple[Resource | None, dict[str, Any] | None]:
    providers = [link.resource for link in _active_links(service)]
    found, sure = _pick(name, providers, lambda r: [r.name, r.description or ""])
    if len(found) == 1 and sure:
        return found[0], None
    note = ""
    if not found or not sure:
        everyone = db.scalars(select(Resource).where(Resource.status == RecordStatus.ACTIVE)).all()
        elsewhere, also_sure = _pick(name, everyone, lambda r: [r.name, r.description or ""])
        if also_sure and elsewhere:
            note = f"{', '.join(r.name for r in elsewhere)} does not offer {service.name}; it is offered by the options. "
            found, sure = [], False
    options = [r.name for r in found or providers]
    return None, _ask(ctx, "person", name, options, sure=sure and bool(found), note=note)


def _nearest_free(
    db: Session, user: User, ctx: ToolContext, service: Service, resource: Resource | None, start: datetime,
    duration: int | None, quantity: int, moving: Booking | None = None,
) -> list[dict[str, Any]]:  # fmt: skip
    """The free times closest to the one asked for (that day and the next), also shown as buttons."""
    try:
        found = find_available_slots(
            db, user, ctx,
            FindSlotsIn(
                service_id=service.id, date=start.date(), days=2, resource_id=resource.id if resource else None,
                quantity=quantity, duration_minutes=duration, rescheduling_booking_id=moving.id if moving else None,
            ),
        )  # fmt: skip
    except AppError:
        db.rollback()
        return []
    times = [(day, t) for day in found["days"] for t in day["times"]]
    times.sort(key=lambda dt: abs(datetime.fromisoformat(dt[1]["start"]) - start.replace(tzinfo=None)))
    nearest = sorted(times[:6], key=lambda dt: dt[1]["start"])
    ctx.choices = [f"{day['label']}, {t['label']}" for day, t in nearest]
    return [{"start": t["start"], "label": f"{day['label']}, {t['label']}", "with": [r["name"] for r in t["resources"]]} for day, t in nearest]


def _hold(
    db: Session, user: User, ctx: ToolContext, args: HoldBookingIn, service: Service, resource: Resource | None,
    moving: Booking | None = None,
) -> dict[str, Any]:  # fmt: skip
    try:
        result = hold_booking(db, user, ctx, args)
    except AppError as exc:
        db.rollback()
        result = _error(exc.code, exc.message)
    if result.get("error") and result["error"] not in _NO_OTHER_TIMES:
        result["nearest_free"] = _nearest_free(db, user, ctx, service, resource, args.start, args.duration_minutes, args.quantity, moving)
        result["next_step"] = (
            "Say in one sentence why that time can't be booked, and ask which of the nearest free times they want; "
            "they are shown as buttons. When they pick one, call the same tool again with that time."
        )
    return result


class BookIn(_In):
    service: str = Field(max_length=100)
    date: date
    time: time
    person: str | None = Field(default=None, max_length=100)
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    quantity: int = Field(default=1, ge=1, le=100)


def book(db: Session, user: User, ctx: ToolContext, args: BookIn) -> dict[str, Any]:
    service, question = _service(db, ctx, args.service)
    if question:
        return question
    resource = None
    if args.person:
        resource, question = _person(db, ctx, service, args.person)
        if question:
            return question
    hold = HoldBookingIn(
        service_id=service.id,
        start=datetime.combine(args.date, args.time),
        resource_id=resource.id if resource else None,
        duration_minutes=args.duration_minutes,
        quantity=args.quantity,
    )
    return _hold(db, user, ctx, hold, service, resource)


class WhichBookingIn(_In):
    # Which of the customer's bookings: its id, or what they said about it.
    booking_id: uuid.UUID | None = None
    on_date: date | None = None
    around_time: time | None = None
    query: str | None = Field(default=None, max_length=100)


def _which(db: Session, user: User, ctx: ToolContext, args: WhichBookingIn) -> tuple[Booking | None, dict[str, Any] | None]:
    if args.booking_id:
        return _mine(db, user, args.booking_id), None
    found = list_my_bookings(
        db, user, ctx, ListBookingsIn(on_date=args.on_date, around_time=args.around_time, query=args.query, limit=MAX_BOOKINGS)
    )
    bookings = found["bookings"]
    if len(bookings) == 1:
        return _mine(db, user, uuid.UUID(bookings[0]["id"])), None
    if not bookings:
        return None, {
            "found": False,
            "upcoming": found.get("other_upcoming", []),
            "next_step": "Say you could not find that booking, list their upcoming bookings (upcoming) and ask which one they mean.",
        }
    ctx.choices = [f"{b['service']}, {b['date']}, {b['time'].split(' – ')[0]}" for b in bookings][:6]
    return None, {
        "which": bookings,
        "next_step": "Several bookings match: ask which one in one short sentence; they are shown as buttons. "
        "Then call the tool again with its booking_id.",
    }


class MoveIn(WhichBookingIn):
    new_date: date | None = None
    new_time: time | None = None
    person: str | None = Field(default=None, max_length=100)
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)


def move(db: Session, user: User, ctx: ToolContext, args: MoveIn) -> dict[str, Any]:
    booking, answer = _which(db, user, ctx, args)
    if answer:
        return answer
    if args.new_date is None and args.new_time is None and args.person is None and args.duration_minutes is None:
        return _error("NOTHING_TO_CHANGE", "Ask what to change: the day, the time, the person or the length.")
    rules = settings_service.get_rules(db)
    local = booking.start_datetime.astimezone(resource_timezone(booking.primary_resource, rules))
    start = datetime.combine(args.new_date or local.date(), args.new_time or local.time().replace(tzinfo=None))
    service, resource = booking.service, booking.primary_resource
    if args.person:
        resource, question = _person(db, ctx, service, args.person)
        if question:
            return question
    length = _minutes(booking)
    duration = args.duration_minutes or length
    flags = bookings_out(db, [booking], user, rules, utcnow())[0]
    new = HoldBookingIn(service_id=service.id, start=start, resource_id=resource.id, duration_minutes=duration, quantity=booking.quantity)

    if flags.held_in_chat:
        # Not confirmed yet: the hold itself is swapped for the new one.
        return _hold(db, user, ctx, new.model_copy(update={"replaces_booking_id": booking.id}), service, resource, moving=booking)

    if duration != length:
        # A move keeps a booking's length: hold a new one of the new length and offer to cancel this one.
        if not flags.can_cancel:
            return _error("CANNOT_CHANGE_LENGTH", f"A move keeps the length ({length} minutes), and this booking can no longer be cancelled online.")
        result = _hold(db, user, ctx, new, service, resource)
        if not result.get("held"):
            return result
        result["cancel_old"] = propose_cancellation(db, user, ctx, CancelIn(booking_id=booking.id))
        result["next_step"] = (
            f"A move keeps a booking's length ({length} minutes), so a new {duration}-minute session is held and cancelling "
            "the old booking is offered. Say so: the customer confirms the new one and the cancellation on their cards."
        )
        return result

    try:
        result = propose_reschedule(db, user, ctx, RescheduleIn(booking_id=booking.id, new_start=start, resource_id=resource.id))
    except AppError as exc:
        db.rollback()
        result = _error(exc.code, exc.message)
    if result.get("error") and result["error"] not in _NO_OTHER_TIMES:
        result["nearest_free"] = _nearest_free(db, user, ctx, service, resource, start, length, booking.quantity, moving=booking)
        result["next_step"] = "Say that time is not free and ask which of the nearest free times they want; they are shown as buttons."
    return result


def cancel(db: Session, user: User, ctx: ToolContext, args: WhichBookingIn) -> dict[str, Any]:
    booking, answer = _which(db, user, ctx, args)
    if answer:
        return answer
    return propose_cancellation(db, user, ctx, CancelIn(booking_id=booking.id))


class AvailabilityIn(_In):
    date: date
    days: int = Field(default=1, ge=1, le=7)
    service: str | None = Field(default=None, max_length=100)
    person: str | None = Field(default=None, max_length=100)
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    quantity: int = Field(default=1, ge=1, le=100)


def check_availability(db: Session, user: User, ctx: ToolContext, args: AvailabilityIn) -> dict[str, Any]:
    if not args.service:
        return list_services(db, user, ctx, ListServicesIn(on_date=args.date))
    service, question = _service(db, ctx, args.service)
    if question:
        return question
    resource = None
    if args.person:
        resource, question = _person(db, ctx, service, args.person)
        if question:
            return question
    found = find_available_slots(
        db, user, ctx,
        FindSlotsIn(
            service_id=service.id, date=args.date, days=args.days, resource_id=resource.id if resource else None,
            quantity=args.quantity, duration_minutes=args.duration_minutes,
        ),
    )  # fmt: skip
    return {"service": service.name, "person": resource.name if resource else None, **found}


class ContactAdminIn(_In):
    summary: str = Field(max_length=600)


# How long the customer has to press Send on the card.
HANDOFF_MINUTES = 60


def contact_admin(db: Session, user: User, ctx: ToolContext, args: ContactAdminIn) -> dict[str, Any]:
    """Offer to send the customer's problem to the admin. Nothing is sent until they press Send."""
    from app.services.chat import help

    summary = help.check_description(args.summary)
    help.check_allowed(db, user)
    now = utcnow()
    proposal = ChatProposal(
        conversation_id=ctx.conversation_id,
        user_id=user.id,
        turn_id=ctx.turn_id,
        kind=ChatProposalKind.HANDOFF,
        status=ChatProposalStatus.PENDING,
        payload={"summary": summary},
        expires_at=now + timedelta(minutes=HANDOFF_MINUTES),
        created_at=now,
        updated_at=now,
    )
    db.add(proposal)
    db.commit()
    ctx.proposal_ids.append(proposal.id)
    hours = get_settings().chat_help_response_hours
    return {
        "card_shown": True,
        "summary": summary,
        "next_step": f"Say you have written down their problem: when they press Send to admin on the card, the admin "
        f"will contact them by e-mail or phone within {hours} hours. Nothing is sent before that.",
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
        """In the OpenAI function-calling format Groq uses."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "additionalProperties": False, **self.schema},
            },
        }


_UUID = {"type": "string", "description": "An id copied from an earlier tool result; leave out when not known"}
_DATE = {"type": "string", "description": "YYYY-MM-DD, from the turn note's list of days"}
_TIME = {"type": "string", "description": "HH:MM, 24-hour, at the place of the booking"}
_NAME = {"type": "string", "description": "As the customer said it or as in the catalogue; typos are fine"}
_WHICH = {
    "booking_id": _UUID,
    "on_date": {"type": "string", "description": "The booking's date, YYYY-MM-DD, if the customer said it"},
    "around_time": {"type": "string", "description": "The booking's time, HH:MM 24-hour, if the customer said it"},
    "query": {"type": "string", "description": "The booking's service or person, if the customer said it"},
}
_LENGTH = {"type": "integer", "minimum": 1, "description": "Only when the customer asked for a length: 'two hours' = 120"}

TOOLS = [
    Tool(
        "book",
        "Book a new appointment and hold it for the customer, who sees a card with Confirm / Delete. Pass the service "
        "and person as said; leave the person out to take any free one. It resolves the names, and when the time is not "
        "free it returns the nearest free times (shown as buttons). When it returns needs, ask that question.",
        {
            "properties": {
                "service": _NAME,
                "date": _DATE,
                "time": _TIME,
                "person": _NAME,
                "duration_minutes": _LENGTH,
                "quantity": {"type": "integer", "minimum": 1, "description": "Places, for group sessions"},
            },
            "required": ["service", "date", "time"],
        },
        BookIn,
        book,
        "Holding your booking…",
    ),
    Tool(
        "move",
        "Change one of the customer's bookings: its day, time, person or length. Say which booking (booking_id, or what "
        "the customer said about it) and only what changes. It handles bookings still waiting for Confirm and length "
        "changes; nothing changes until the customer presses Confirm on the card.",
        {
            "properties": {
                **_WHICH,
                "new_date": _DATE,
                "new_time": _TIME,
                "person": _NAME,
                "duration_minutes": _LENGTH,
            }
        },
        MoveIn,
        move,
        "Preparing the change…",
    ),
    Tool(
        "cancel",
        "Offer to cancel one of the customer's bookings (booking_id, or what the customer said about it). Nothing "
        "changes until the customer presses Confirm on the card.",
        {"properties": _WHICH},
        WhichBookingIn,
        cancel,
        "Preparing the cancellation…",
    ),
    Tool(
        "check_availability",
        "Free times. With a service (and optionally a person): its free start times from date, for up to 7 days. "
        "Without one: every service with its free times that day.",
        {
            "properties": {
                "date": _DATE,
                "days": {"type": "integer", "minimum": 1, "maximum": 7},
                "service": _NAME,
                "person": _NAME,
                "duration_minutes": _LENGTH,
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["date"],
        },
        AvailabilityIn,
        check_availability,
        "Checking free times…",
    ),
    Tool(
        "my_bookings",
        "The customer's own bookings (upcoming by default), with whether each can still be cancelled or moved. Pass "
        "on_date, around_time and query to find the one they mean.",
        {
            "properties": {
                "scope": {"type": "string", "enum": ["upcoming", "past", "cancelled", "waitlisted", "all"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_BOOKINGS},
                "on_date": _WHICH["on_date"],
                "around_time": _WHICH["around_time"],
                "query": _WHICH["query"],
            }
        },
        ListBookingsIn,
        list_my_bookings,
        "Looking at your bookings…",
    ),
    Tool(
        "contact_admin",
        "Offer to send the customer's problem to the admin, who then contacts them. Only for a real problem you cannot "
        "solve with your other tools (a payment, a complaint, something that went wrong), or when the customer insists on "
        "talking to a person. summary: the problem in one or two sentences, in the customer's words. The customer sees a "
        "card and must press Send; nothing is sent before.",
        {"properties": {"summary": {"type": "string"}}, "required": ["summary"]},
        ContactAdminIn,
        contact_admin,
        "Writing down your problem…",
    ),
    Tool(
        "offer_choices",
        "Show 2 to 6 short options of your own as buttons under your reply (e.g. yes/no). A click sends the option back "
        "as the customer's answer. The other tools set their own buttons.",
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
