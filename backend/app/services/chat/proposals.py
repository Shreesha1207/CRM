"""The cards the assistant shows, and what the customer's Confirm / Delete / Decline does.

A HOLD card is a booking the assistant already made and holds for the customer.
RESCHEDULE and CANCEL cards change an existing booking only when confirmed.
Every change runs through the booking engine as the customer.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, BookingRejected, NotFound
from app.core.timeutils import utcnow
from app.models import Booking, ChatConversation, ChatProposal, Resource, User
from app.models.enums import BookingStatus, ChatProposalKind, ChatProposalStatus
from app.services import booking_mail, settings_service
from app.services.availability import resource_timezone
from app.services.booking_engine import BookingEngine
from app.services.notifications import _clock

K, P = ChatProposalKind, ChatProposalStatus


def _money(value) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _when(start: datetime, end: datetime, zone) -> dict[str, str]:
    start, end = start.astimezone(zone), end.astimezone(zone)
    if end.date() != start.date():
        time = f"{_clock(start)} – {end:%a %d %b}, {_clock(end)}"
    else:
        time = f"{_clock(start)} – {_clock(end)}"
    return {"date": f"{start:%a %d %b %Y}", "time": time, "zone": zone.key}


def _facts_when(facts: booking_mail.Facts) -> dict[str, str]:
    return {"date": facts.when.date, "time": facts.when.time, "zone": facts.when.zone}


def status_of(proposal: ChatProposal, now: datetime) -> ChatProposalStatus:
    """An open card past its time counts as expired, even before the job tidies it up."""
    if proposal.status == P.PENDING and proposal.expires_at <= now:
        return P.EXPIRED
    return proposal.status


def card(db: Session, proposal: ChatProposal, user: User, now: datetime | None = None) -> dict[str, Any]:
    """What the customer sees: the same details as the booking e-mails."""
    now = now or utcnow()
    rules = settings_service.get_rules(db)
    status = status_of(proposal, now)
    out: dict[str, Any] = {
        "id": str(proposal.id),
        "kind": proposal.kind.value,
        "status": status.value,
        "expires_at": proposal.expires_at.isoformat(),
        "result_message": proposal.result_message,
        "booking_id": str(proposal.result_booking_id or proposal.booking_id) if (proposal.result_booking_id or proposal.booking_id) else None,
    }
    if proposal.kind == K.HANDOFF:
        out.update(summary=proposal.payload.get("summary", ""), response_hours=get_settings().chat_help_response_hours)
        return out
    booking = db.get(Booking, proposal.booking_id) if proposal.booking_id else None
    if booking is None:
        return out
    result = db.get(Booking, proposal.result_booking_id) if proposal.result_booking_id else None
    shown = result or booking
    previous = booking if proposal.kind == K.RESCHEDULE and result is not None else None
    facts = booking_mail.facts(db, shown, user.id, previous)
    out.update(
        business=facts.business,
        service=facts.service,
        resource=facts.resource,
        quantity=facts.quantity,
        when=_facts_when(facts),
        was=None,
        place=facts.place,
        map_url=facts.map_url,
        price=_money(shown.price),
        booking_status=shown.status.value,
    )
    if facts.was is not None:
        out["was"] = {"date": facts.was.date, "time": facts.was.time, "zone": facts.was.zone}
    if proposal.kind == K.RESCHEDULE and result is None:
        # Not moved yet: show the new time, and the current one as "was".
        target = db.get(Resource, uuid.UUID(proposal.payload["resource_id"])) or booking.primary_resource
        zone = resource_timezone(target, rules)
        new_start = datetime.fromisoformat(proposal.payload["new_start"])
        out["resource"] = target.name
        out["was"] = out["when"]
        out["when"] = _when(new_start, new_start + (booking.end_datetime - booking.start_datetime), zone)
    return out


def summary(card_: dict[str, Any]) -> str:
    if "when" not in card_:
        return "a booking"
    when = card_["when"]
    return f"{card_['service']} with {card_['resource']}, {when['date']}, {when['time']} ({when['zone']})"


def _own(db: Session, proposal_id: uuid.UUID, user: User, *, lock: bool = False) -> ChatProposal:
    stmt = select(ChatProposal).where(ChatProposal.id == proposal_id, ChatProposal.user_id == user.id)
    proposal = db.scalar(stmt.with_for_update() if lock else stmt)
    if proposal is None:
        raise NotFound("Card")
    return proposal


def _close(db: Session, proposal: ChatProposal, status: ChatProposalStatus, message: str | None = None) -> None:
    proposal.status = status
    proposal.result_message = message
    proposal.updated_at = utcnow()
    db.commit()


def _expire(db: Session, proposal: ChatProposal, user: User) -> None:
    if proposal.kind == K.HOLD and proposal.booking_id:
        try:
            BookingEngine(db, user).release_chat_hold(
                proposal.booking_id, outcome=P.EXPIRED, reason="Not confirmed in time"
            )
            return
        except AppError:
            db.rollback()
    _close(db, db.get(ChatProposal, proposal.id), P.EXPIRED, "Not confirmed in time")


def confirm(db: Session, proposal_id: uuid.UUID, user: User, summary: str | None = None) -> ChatProposal:
    """Confirm a card. For a HANDOFF, `summary` is the description as the customer finally wrote it."""
    proposal = _own(db, proposal_id, user)
    if proposal.status != P.PENDING:
        raise BookingRejected("ALREADY_ANSWERED", "This card has already been answered", status_code=409)
    now = utcnow()
    if proposal.expires_at <= now:
        _expire(db, proposal, user)
        raise BookingRejected("HOLD_EXPIRED", "This has expired, so nothing was changed. Ask the assistant again.", status_code=409)
    engine = BookingEngine(db, user)
    pid = proposal.id
    if proposal.kind == K.HANDOFF:
        return _send_to_admin(db, proposal, user, summary)
    try:
        if proposal.kind == K.HOLD:
            engine.confirm_chat_hold(proposal.booking_id)  # closes the card in the same transaction
            return db.get(ChatProposal, pid)
        if proposal.kind == K.RESCHEDULE:
            moved = engine.reschedule(
                proposal.booking_id,
                new_start=datetime.fromisoformat(proposal.payload["new_start"]),
                new_resource_id=uuid.UUID(proposal.payload["resource_id"]),
                reason="Moved with the booking assistant",
            )
            proposal = db.get(ChatProposal, pid)
            proposal.result_booking_id = moved.id
        else:
            engine.cancel(proposal.booking_id, reason="Cancelled with the booking assistant")
            proposal = db.get(ChatProposal, pid)
        _close(db, proposal, P.CONFIRMED)
    except BookingRejected as exc:
        db.rollback()
        _close(db, db.get(ChatProposal, pid), P.FAILED, exc.message)
    return db.get(ChatProposal, pid)


def _send_to_admin(db: Session, proposal: ChatProposal, user: User, summary: str | None) -> ChatProposal:
    from app.services.chat import help

    pid = proposal.id
    # A description that isn't good enough is refused with the card left open, so it can be fixed.
    text = help.check_description(summary if summary is not None else proposal.payload["summary"])
    try:
        request = help.request_help(db, user, db.get(ChatConversation, proposal.conversation_id), text)
    except AppError as exc:
        db.rollback()
        _close(db, db.get(ChatProposal, pid), P.FAILED, exc.message)
        return db.get(ChatProposal, pid)
    proposal = db.get(ChatProposal, pid)
    proposal.payload = {**proposal.payload, "summary": text, "request_id": str(request.id)}
    _close(db, proposal, P.CONFIRMED, help.confirmation(db, request))
    return db.get(ChatProposal, pid)


def decline(db: Session, proposal_id: uuid.UUID, user: User) -> ChatProposal:
    """Delete a held booking, or decline an offered change."""
    proposal = _own(db, proposal_id, user)
    if proposal.status != P.PENDING:
        raise BookingRejected("ALREADY_ANSWERED", "This card has already been answered", status_code=409)
    pid = proposal.id
    if proposal.kind == K.HOLD and proposal.booking_id:
        try:
            BookingEngine(db, user).release_chat_hold(proposal.booking_id, outcome=P.DECLINED, reason="Deleted in the chat")
        except AppError:
            db.rollback()
            _close(db, db.get(ChatProposal, pid), P.DECLINED, "Deleted in the chat")
    else:
        _close(db, proposal, P.DECLINED)
    return db.get(ChatProposal, pid)


def withdraw_turn(db: Session, turn_id: uuid.UUID, user: User) -> None:
    """A turn whose reply was blocked leaves nothing behind: its holds are released."""
    for proposal in db.scalars(
        select(ChatProposal).where(ChatProposal.turn_id == turn_id, ChatProposal.status == P.PENDING)
    ).all():
        if proposal.kind == K.HOLD and proposal.booking_id:
            try:
                BookingEngine(db, user).release_chat_hold(proposal.booking_id, outcome=P.EXPIRED, reason="Withdrawn")
                continue
            except AppError:
                db.rollback()
        _close(db, db.get(ChatProposal, proposal.id), P.EXPIRED, "Withdrawn")


def report(db: Session, conversation_id: uuid.UUID, user: User, now: datetime) -> list[str]:
    """How cards ended since the assistant's last reply, for the next turn note."""
    events = []
    rows = db.scalars(
        select(ChatProposal)
        .where(ChatProposal.conversation_id == conversation_id, ChatProposal.reported_at.is_(None))
        .order_by(ChatProposal.created_at)
    ).all()
    for proposal in rows:
        status = status_of(proposal, now)
        if status == P.PENDING:
            continue
        what = summary(card(db, proposal, user, now))
        message = f": {proposal.result_message}" if proposal.result_message and status == P.FAILED else ""
        if proposal.kind == K.HANDOFF:
            text = {
                P.CONFIRMED: f"The customer sent their problem to the admin. {proposal.result_message}",
                P.DECLINED: "The customer chose not to contact the admin.",
                P.EXPIRED: "The offer to contact the admin expired without an answer.",
                P.FAILED: f"Sending the problem to the admin failed{message}.",
            }[status]
        elif proposal.kind == K.HOLD:
            text = {
                P.CONFIRMED: f"The customer confirmed the held booking {what}. It is booked.",
                P.DECLINED: f"The customer deleted the held booking {what}; its time was released.",
                P.EXPIRED: f"The hold on {what} expired without confirmation; its time was released.",
                P.FAILED: f"Confirming the held booking {what} failed{message}.",
            }[status]
            booking = db.get(Booking, proposal.booking_id) if proposal.booking_id else None
            if status == P.CONFIRMED and booking is not None and booking.status == BookingStatus.PENDING:
                text = f"The customer confirmed {what}. It now waits for the business to approve it."
        else:
            verb = "moving" if proposal.kind == K.RESCHEDULE else "cancelling"
            text = {
                P.CONFIRMED: f"The customer confirmed {verb} {what}. Done.",
                P.DECLINED: f"The customer declined {verb} {what}. Nothing changed.",
                P.EXPIRED: f"The offer of {verb} {what} expired. Nothing changed.",
                P.FAILED: f"{verb.capitalize()} {what} failed{message}. Nothing changed.",
            }[status]
        events.append(text)
        proposal.reported_at = now
    db.commit()
    return events
