"""Bookings the assistant holds for a customer until they confirm or delete them."""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import ChatProposal, User
from app.models.enums import ChatProposalKind, ChatProposalStatus
from app.services.booking_engine import BookingEngine

log = logging.getLogger("chat")


def held_in_chat(db: Session, booking_ids: list) -> set:
    """Which of these bookings the assistant holds for their customer to confirm."""
    if not booking_ids:
        return set()
    return set(
        db.scalars(
            select(ChatProposal.booking_id).where(
                ChatProposal.booking_id.in_(booking_ids),
                ChatProposal.kind == ChatProposalKind.HOLD,
                ChatProposal.status == ChatProposalStatus.PENDING,
            )
        )
    )


def release_expired(db: Session, now: datetime) -> int:
    """Free the time of holds nobody confirmed in time. Returns how many were released."""
    expired = db.scalars(
        select(ChatProposal).where(
            ChatProposal.kind == ChatProposalKind.HOLD,
            ChatProposal.status == ChatProposalStatus.PENDING,
            ChatProposal.expires_at <= now,
        )
    ).all()
    released = 0
    for hold in expired:
        owner = db.get(User, hold.user_id)
        if owner is None or hold.booking_id is None:
            continue
        try:
            BookingEngine(db, owner, now=now).release_chat_hold(
                hold.booking_id, outcome=ChatProposalStatus.EXPIRED, reason="Not confirmed in time"
            )
            released += 1
        except AppError:
            db.rollback()  # already confirmed or cancelled meanwhile
            log.info("Hold %s was already closed", hold.id)
    # Offered changes that nobody answered: nothing to undo, just close them.
    for offer in db.scalars(
        select(ChatProposal).where(
            ChatProposal.kind != ChatProposalKind.HOLD,
            ChatProposal.status == ChatProposalStatus.PENDING,
            ChatProposal.expires_at <= now,
        )
    ).all():
        offer.status = ChatProposalStatus.EXPIRED
        offer.updated_at = now
    db.commit()
    return released
