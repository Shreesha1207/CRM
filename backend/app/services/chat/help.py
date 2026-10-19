"""Contacting the admin from the booking assistant.

Only for a real problem, and only when the customer asks: they confirm a short
description (their own words, or the assistant's summary of them), and the admin
gets it with the whole chat for context. A customer has at most one open request
and a few a week; automatic failures, thumbs-down and blocked messages never
reach the admin. Admins contact the customer themselves, by e-mail or phone.
"""

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.permissions import ROLE_PERMISSIONS, Permission
from app.core.timeutils import get_zone, utcnow
from app.models import ChatConversation, ChatHelpRequest, ChatMessage, ChatProposal, ChatSubscription, User
from app.models.enums import (
    ChatHelpStatus,
    ChatProposalKind,
    ChatRole,
    NotificationChannel,
    NotificationType,
    UserStatus,
)
from app.services import settings_service
from app.services.chat.guard import looks_like_code
from app.services.notifications import _clock, notify

MIN_CHARS, MAX_CHARS = 20, 500
IN_APP = (NotificationChannel.IN_APP,)
BOTH = (NotificationChannel.IN_APP, NotificationChannel.EMAIL)


def _local(db: Session, moment: datetime) -> str:
    zone = get_zone(settings_service.get_rules(db).default_timezone)
    at = moment.astimezone(zone)
    return f"{at:%a %d %b}, {_clock(at)}"


def chat_history(db: Session, user: User, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    """The whole chat as the customer saw it, plus what was blocked and why."""
    from app.services.chat import agent

    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.display.is_not(None))
        .order_by(ChatMessage.seq)
    ).all()
    items = []
    for row in rows:
        item = agent.display_item(db, user, row)
        item["kind"] = "notice" if row.role == ChatRole.NOTICE else item["role"]
        item["blocked"] = row.blocked_reason
        items.append(item)
    return items


def _card_line(card: dict[str, Any]) -> str:
    if card["kind"] == "HANDOFF":
        what = f"contact the admin: {card.get('summary', '')}"
    elif "when" in card:
        when = card["when"]
        what = f"{card['kind'].lower()} {card['service']} with {card['resource']}, {when['date']}, {when['time']} ({when['zone']})"
    else:
        what = card["kind"].lower()
    return f"[Card: {what}; {card['status'].lower()}]"


def history_text(db: Session, items: list[dict[str, Any]]) -> str:
    """The chat as plain text, oldest first, for the admin's e-mail."""
    who = {"user": "Customer", "assistant": "Assistant", "notice": "Notice"}
    lines = []
    for item in items:
        lines.append(f"{who[item['kind']]}, {_local(db, datetime.fromisoformat(item['created_at']))}: {item['text']}")
        if item.get("blocked"):
            lines.append(f"  (blocked: {item['blocked']})")
        lines += [f"  {_card_line(card)}" for card in item.get("cards", [])]
    return "\n".join(lines) or "(no messages)"


def reviewers(db: Session) -> list[User]:
    """Everyone who handles help requests (admins, super admins, managers)."""
    roles = [role for role, granted in ROLE_PERMISSIONS.items() if Permission.CHAT_REVIEW in granted]
    return list(db.scalars(select(User).where(User.role.in_(roles), User.status == UserStatus.ACTIVE)))


def open_request(db: Session, user_id: uuid.UUID) -> ChatHelpRequest | None:
    return db.scalar(select(ChatHelpRequest).where(ChatHelpRequest.user_id == user_id, ChatHelpRequest.status == ChatHelpStatus.OPEN))


# Words that only ask for someone, without saying what is wrong.
_WHO = {"admin", "administrator", "someone", "somebody", "person", "human", "team", "staff", "manager", "support", "agent", "owner"}
_ASKING = _WHO | {
    "customer", "user", "wants", "want", "would", "like", "to", "the", "a", "an", "i", "please", "contact", "talk", "speak",
    "reach", "call", "message", "connect", "get", "with", "in", "touch", "me", "can", "could", "you", "need", "needs", "asks",
    "asked", "asking", "for", "help", "let", "my", "is", "be", "and", "them", "they", "real", "now", "urgent", "hi", "hello",
}  # fmt: skip


def is_only_a_request_for_contact(summary: str) -> bool:
    """ "contact admin", "Customer wants to contact admin", "I want to talk to someone": no problem in it."""
    words = re.findall(r"[a-z']+", summary.lower())
    return any(w in _WHO for w in words) and len([w for w in words if w not in _ASKING]) <= 2


def check_description(summary: str) -> str:
    summary = " ".join(summary.split())
    if is_only_a_request_for_contact(summary):
        raise AppError("HELP_DESCRIPTION", "Please say what went wrong, so the admin can help.", status_code=422)
    if not MIN_CHARS <= len(summary) <= MAX_CHARS:
        raise AppError("HELP_DESCRIPTION", f"Please describe the problem in {MIN_CHARS} to {MAX_CHARS} characters.", status_code=422)
    if looks_like_code(summary):
        raise AppError("HELP_DESCRIPTION", "Please describe the problem in plain words.", status_code=422)
    return summary


def check_allowed(db: Session, user: User) -> None:
    """Refuse before anything is shown or sent: paused, already waiting, or too many this week."""
    now = utcnow()
    sub = db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user.id))
    if sub is not None and sub.paused_until and sub.paused_until > now:
        raise AppError("CHAT_PAUSED", "The assistant is paused for your account for a while.", status_code=429)
    waiting = open_request(db, user.id)
    if waiting is not None:
        raise AppError(
            "HELP_ALREADY_OPEN",
            f"Your request is already with the admin; they will contact you by {_local(db, waiting.due_at)}.",
            status_code=409,
        )
    this_week = db.scalar(
        select(func.count()).select_from(ChatHelpRequest).where(
            ChatHelpRequest.user_id == user.id, ChatHelpRequest.created_at > now - timedelta(days=7)
        )
    )
    if this_week >= get_settings().chat_help_requests_per_week:
        raise AppError(
            "HELP_LIMIT_REACHED",
            "You have contacted the admin several times this week. Please contact the business directly.",
            status_code=429,
        )


def confirmation(db: Session, request: ChatHelpRequest) -> str:
    hours = get_settings().chat_help_response_hours
    return (
        "Your issue has been passed to the admin. They will contact you by e-mail or phone "
        f"within {hours} hours (by {_local(db, request.due_at)})."
    )


def _add_notice(db: Session, conversation: ChatConversation, text: str, request: ChatHelpRequest) -> None:
    seq = (db.scalar(select(func.max(ChatMessage.seq)).where(ChatMessage.conversation_id == conversation.id)) or 0) + 1
    db.add(
        ChatMessage(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            turn_id=uuid.uuid4(),
            seq=seq,
            role=ChatRole.NOTICE,
            content=None,
            display={"text": text, "help_request_id": str(request.id)},
            created_at=utcnow(),
        )
    )


def request_help(db: Session, user: User, conversation: ChatConversation, summary: str) -> ChatHelpRequest:
    """Send the customer's problem and the whole chat to the admin, and tell the customer when to expect them."""
    summary = check_description(summary)
    check_allowed(db, user)
    now = utcnow()
    history = chat_history(db, user, conversation.id)
    request = ChatHelpRequest(
        conversation_id=conversation.id,
        user_id=user.id,
        summary=summary,
        transcript=history,
        status=ChatHelpStatus.OPEN,
        created_at=now,
        due_at=now + timedelta(hours=get_settings().chat_help_response_hours),
    )
    db.add(request)
    try:
        db.flush()
    except IntegrityError:  # another request was opened at the same moment
        db.rollback()
        check_allowed(db, user)
        raise
    text = confirmation(db, request)
    _add_notice(db, conversation, text, request)
    notify(
        db,
        user_id=user.id,
        type=NotificationType.CHAT_HELP_RECEIVED,
        title="Your request was passed to the admin",
        body=f"{text}\nYou wrote: {summary}",
        channels=IN_APP,
        link=f"/chat?c={conversation.id}",
        dedupe_key=f"chat-help-received:{request.id}",
    )
    contact = " · ".join(part for part in (user.email, user.phone) if part)
    due = _local(db, request.due_at)
    for reviewer in reviewers(db):
        notify(
            db,
            user_id=reviewer.id,
            type=NotificationType.CHAT_HELP_REQUESTED,
            title=f"Customer needs help: {user.name}",
            body=f"{summary}\nPlease contact them by {due}.",
            email_body=(
                f"{user.name} asked the admin to contact them from the booking assistant.\n\n"
                f"Their problem:\n{summary}\n\n"
                f"Contact: {contact}\nPlease contact them by {due}.\n\n"
                f"The whole chat:\n{history_text(db, history)}\n\n"
                f"Open it in the admin console: {get_settings().frontend_url.rstrip('/')}/admin/help/{request.id}"
            ),
            channels=BOTH,
            link=f"/admin/help/{request.id}",
            dedupe_key=f"chat-help:{request.id}:{reviewer.id}",
        )
    db.commit()
    return request


def remind_overdue(db: Session, now: datetime) -> int:
    """Remind the admins, once, about requests still open past the time the customer was promised."""
    overdue = db.scalars(
        select(ChatHelpRequest).where(
            ChatHelpRequest.status == ChatHelpStatus.OPEN,
            ChatHelpRequest.due_at <= now,
            ChatHelpRequest.reminded_at.is_(None),
        )
    ).all()
    for request in overdue:
        for reviewer in reviewers(db):
            notify(
                db,
                user_id=reviewer.id,
                type=NotificationType.CHAT_HELP_OVERDUE,
                title=f"Overdue: {request.user.name} is still waiting",
                body=f"They were promised a reply by {_local(db, request.due_at)}.\n{request.summary}",
                channels=BOTH,
                link=f"/admin/help/{request.id}",
                dedupe_key=f"chat-help-overdue:{request.id}:{reviewer.id}",
            )
        request.reminded_at = now
    db.commit()
    return len(overdue)


def resolution_text(request: ChatHelpRequest) -> str:
    wrote = request.summary if len(request.summary) <= 120 else request.summary[:117].rstrip() + "…"
    text = f'The admin has resolved your request ("{wrote}").'
    return f'{text} Their note: "{request.resolution_note}"' if request.resolution_note else text


def resolve(db: Session, request: ChatHelpRequest, admin: User, note: str | None) -> ChatHelpRequest:
    if request.status != ChatHelpStatus.OPEN:
        raise AppError("ALREADY_RESOLVED", "This request is already resolved", status_code=409)
    request.status = ChatHelpStatus.RESOLVED
    request.resolved_at = utcnow()
    request.resolved_by_id = admin.id
    request.resolution_note = (note or "").strip() or None
    text = resolution_text(request)
    # The customer sees it in the chat they asked from, on the card they sent, and by e-mail.
    _add_notice(db, db.get(ChatConversation, request.conversation_id), text, request)
    card = db.scalar(
        select(ChatProposal).where(ChatProposal.kind == ChatProposalKind.HANDOFF, ChatProposal.payload["request_id"].astext == str(request.id))
    )
    if card is not None:
        card.result_message = "Resolved by the admin." + (f" Their note: {request.resolution_note}" if request.resolution_note else "")
    notify(
        db,
        user_id=request.user_id,
        type=NotificationType.CHAT_HELP_RESOLVED,
        title="Your request to the admin was resolved",
        body=f"{text}\nYou wrote: {request.summary}",
        channels=BOTH,
        link=f"/chat?c={request.conversation_id}",
        dedupe_key=f"chat-help-resolved:{request.id}",
    )
    db.commit()
    return request
