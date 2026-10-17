"""One turn of the booking assistant: from the customer's message to the reply they see.

    check the message (guards) -> model <-> tools, a few rounds -> check the reply (guards)
    -> show it, or throw the whole turn away and show the fixed reply

Everything the model sees is stored, and replayed unchanged on later turns. A
turn that is blocked is kept for review but never sent to the model again, and
any booking it held is released.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import rate_limiter
from app.core.timeutils import utcnow
from app.db.session import SessionLocal
from app.models import ChatConversation, ChatMessage, ChatProposal, ChatSubscription, User
from app.models.enums import ChatRole
from app.services import settings_service
from app.services.chat import prompt, proposals, tools
from app.services.chat.guard import GuardClient, Verdict
from app.services.chat.llm import ChatModel

log = logging.getLogger("chat")

Emit = Callable[[str, dict[str, Any]], None]

LEASE_SECONDS = 180
STARTERS = ["Book an appointment", "What's free this week?", "My upcoming bookings", "Move a booking"]


def fixed_reply(db: Session) -> str:
    business = settings_service.get_rules(db).business_name
    return (
        f"I can only help with bookings at {business}: finding a time, booking, moving or cancelling an "
        "appointment, or questions about our services."
    )


UNAVAILABLE = "The assistant is unavailable right now. Please try again in a moment, or use the booking page."
TOO_COMPLEX = "Sorry, I couldn't finish that. Could you try again, one step at a time? You can also use the booking page."
REFUSED = "Sorry, I can't help with that. I can find a time, book, move or cancel an appointment, or answer questions about our services."


class ChatLimit(AppError):
    status_code = 429


# ---------------------------------------------------------------- before a turn


def check_limits(db: Session, user: User, conversation: ChatConversation) -> None:
    """Refuse the turn before anything runs (and before anything is paid for)."""
    settings = get_settings()
    now = utcnow()
    sub = db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user.id))
    if sub is not None and sub.paused_until and sub.paused_until > now:
        raise ChatLimit("CHAT_PAUSED", "The assistant is paused for your account for a while after several blocked messages.")
    if not rate_limiter.hit(f"chat:{user.id}", settings.chat_messages_per_minute, 60):
        raise ChatLimit("CHAT_LIMIT_REACHED", "You're sending messages too quickly. Please wait a minute.")
    day_ago = now - timedelta(days=1)
    sent_today = db.scalar(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.user_id == user.id,
            ChatMessage.role == ChatRole.USER,
            ChatMessage.display.is_not(None),
            ChatMessage.created_at > day_ago,
        )
    )
    if sent_today >= settings.chat_daily_message_limit:
        raise ChatLimit("CHAT_LIMIT_REACHED", "You've reached today's message limit for the assistant. Please use the booking page.")
    tokens_today = db.scalar(
        select(func.coalesce(func.sum(ChatMessage.input_tokens + ChatMessage.output_tokens), 0)).where(
            ChatMessage.user_id == user.id, ChatMessage.created_at > day_ago
        )
    )
    if tokens_today >= settings.chat_daily_token_limit:
        raise ChatLimit("CHAT_LIMIT_REACHED", "You've reached today's limit for the assistant. Please use the booking page.")
    in_conversation = db.scalar(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.role == ChatRole.USER,
            ChatMessage.display.is_not(None),
        )
    )
    if in_conversation >= settings.chat_max_messages_per_conversation:
        raise ChatLimit("CHAT_LIMIT_REACHED", "This conversation is long. Please start a new chat.")


def take_lease(db: Session, conversation: ChatConversation) -> None:
    now = utcnow()
    taken = db.execute(
        update(ChatConversation)
        .where(
            ChatConversation.id == conversation.id,
            (ChatConversation.busy_until.is_(None)) | (ChatConversation.busy_until < now),
        )
        .values(busy_until=now + timedelta(seconds=LEASE_SECONDS))
    ).rowcount
    db.commit()
    if not taken:
        raise AppError("CHAT_BUSY", "The assistant is still answering your last message.", status_code=409)


# ---------------------------------------------------------------- history


def _history(db: Session, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.blocked_reason.is_(None),
            ChatMessage.role != ChatRole.NOTICE,
        )
        .order_by(ChatMessage.seq)
    ).all()
    return [{"role": r.role.value, "content": r.content} for r in rows]


def last_reply(db: Session, conversation_id: uuid.UUID) -> str | None:
    row = db.scalar(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role.in_([ChatRole.ASSISTANT, ChatRole.NOTICE]),
            ChatMessage.display.is_not(None),
        )
        .order_by(ChatMessage.seq.desc())
        .limit(1)
    )
    return row.display.get("text") if row is not None else None


@dataclass
class _Turn:
    db: Session
    conversation: ChatConversation
    user: User
    turn_id: uuid.UUID

    def add(self, role: ChatRole, content: Any, **fields) -> ChatMessage:
        seq = (self.db.scalar(select(func.max(ChatMessage.seq)).where(ChatMessage.conversation_id == self.conversation.id)) or 0) + 1
        row = ChatMessage(
            conversation_id=self.conversation.id,
            user_id=self.user.id,
            turn_id=self.turn_id,
            seq=seq,
            role=role,
            content=content,
            created_at=utcnow(),
            **fields,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def block(self, reason: str) -> None:
        """Keep the turn for review, but never send it to the model again."""
        self.db.execute(
            update(ChatMessage).where(ChatMessage.turn_id == self.turn_id).values(blocked_reason=reason)
        )
        self.db.commit()


def _strike(db: Session, user: User, conversation: ChatConversation) -> bool:
    """Count a blocked message; too many in a short time pause the assistant. True when paused."""
    settings = get_settings()
    now = utcnow()
    since = now - timedelta(minutes=settings.chat_strike_window_minutes)
    strikes = db.scalar(
        select(func.count(func.distinct(ChatMessage.turn_id))).where(
            ChatMessage.user_id == user.id,
            ChatMessage.created_at > since,
            ChatMessage.blocked_reason.like("strike %"),
        )
    )
    if strikes < settings.chat_strike_limit:
        return False
    sub = db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user.id))
    if sub is not None:
        sub.paused_until = now + timedelta(minutes=settings.chat_pause_minutes)
    conversation.flagged_at = now
    db.commit()
    log.warning("Chat paused for user %s after %s blocked messages", user.id, strikes)
    return True


# ---------------------------------------------------------------- the turn


def run_turn(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    text: str,
    model: ChatModel,
    guard: GuardClient,
    emit: Emit,
) -> None:
    """Runs one turn and emits `status` and finally one `reply` event. The lease
    taken before the turn is always released."""
    with SessionLocal() as db:
        conversation = db.get(ChatConversation, conversation_id)
        user = db.get(User, user_id)
        turn = _Turn(db, conversation, user, uuid.uuid4())
        try:
            _run(turn, text, model, guard, emit)
        except Exception:
            log.exception("Chat turn failed")
            db.rollback()
            turn.block("error")
            proposals.withdraw_turn(db, turn.turn_id, user)
            notice = turn.add(ChatRole.NOTICE, None, display={"text": UNAVAILABLE})
            emit("reply", _reply(db, user, notice))
        finally:
            db.rollback()
            db.execute(update(ChatConversation).where(ChatConversation.id == conversation_id).values(busy_until=None))
            db.commit()


def _reply(db: Session, user: User, row: ChatMessage) -> dict[str, Any]:
    return display_item(db, user, row)


def _refuse(turn: _Turn, reason: str, message: str, emit: Emit, *, strike: bool) -> None:
    db = turn.db
    turn.block(("strike " if strike else "") + reason)
    proposals.withdraw_turn(db, turn.turn_id, turn.user)
    paused = _strike(db, turn.user, turn.conversation) if strike else False
    choices = [] if paused else STARTERS
    notice = turn.add(ChatRole.NOTICE, None, display={"text": message, "choices": choices, "paused": paused})
    emit("reply", _reply(db, turn.user, notice))


def _run(turn: _Turn, text: str, model: ChatModel, guard: GuardClient, emit: Emit) -> None:
    db, user, conversation = turn.db, turn.user, turn.conversation
    settings = get_settings()
    now = utcnow()
    if conversation.title == "New chat":
        conversation.title = text[:80]
    conversation.updated_at = now
    db.commit()

    previous = last_reply(db, conversation.id)
    turn.add(ChatRole.USER, text, display={"text": text})

    emit("status", {"label": "Reading your message…"})
    verdict: Verdict = guard.screen_input(text, previous)
    if not verdict.allowed:
        message = UNAVAILABLE if verdict.unavailable else fixed_reply(db)
        _refuse(turn, verdict.stored_reason, message, emit, strike=not verdict.unavailable)
        return

    events = proposals.report(db, conversation.id, user, now)
    turn.add(ChatRole.SYSTEM, prompt.turn_note(db, user, now, events))

    system = prompt.system_prompt(db)
    definitions = tools.definitions()
    ctx = tools.ToolContext(user_id=user.id, conversation_id=conversation.id, turn_id=turn.turn_id)
    final = None
    for _round in range(settings.chat_max_tool_rounds):
        emit("status", {"label": "Thinking…"})
        result = model.run_turn(system=system, tools=definitions, messages=_history(db, conversation.id))
        row = turn.add(ChatRole.ASSISTANT, result.content, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        conversation.input_tokens += result.input_tokens
        conversation.output_tokens += result.output_tokens
        db.commit()
        if result.stop_reason == "refusal":
            _refuse(turn, "refusal", REFUSED, emit, strike=False)
            return
        if result.stop_reason == "tool_use" and result.tool_uses:
            outputs = []
            for call in result.tool_uses:
                spec = tools.TOOLS_BY_NAME.get(call["name"])
                if spec is not None and spec.status:
                    emit("status", {"label": spec.status})
                content, is_error = tools.run(call["name"], call.get("input"), ctx)
                outputs.append({"type": "tool_result", "tool_use_id": call["id"], "content": content, **({"is_error": True} if is_error else {})})
            turn.add(ChatRole.USER, outputs)
            continue
        if result.stop_reason in ("end_turn", "stop_sequence"):
            final = row
            break
        log.warning("Chat turn stopped with %s", result.stop_reason)
        break
    if final is None:
        _refuse(turn, "unfinished", TOO_COMPLEX, emit, strike=False)
        return

    reply_text = model_text(final.content)
    emit("status", {"label": "Checking the reply…"})
    verdict = guard.screen_output(reply_text, ctx.choices)
    if not verdict.allowed:
        message = UNAVAILABLE if verdict.unavailable else fixed_reply(db)
        _refuse(turn, verdict.stored_reason, message, emit, strike=not verdict.unavailable)
        return

    final.display = {
        "text": reply_text,
        "choices": ctx.choices,
        "proposal_ids": [str(p) for p in ctx.proposal_ids],
    }
    db.commit()
    emit("reply", _reply(db, user, final))


def model_text(content: list[dict[str, Any]]) -> str:
    return "\n\n".join(b["text"].strip() for b in content if b.get("type") == "text" and b.get("text", "").strip())


# ---------------------------------------------------------------- what the customer sees


def display_item(db: Session, user: User, row: ChatMessage) -> dict[str, Any]:
    display = row.display or {}
    cards = []
    ids = [uuid.UUID(p) for p in display.get("proposal_ids", [])]
    if ids:
        found = {p.id: p for p in db.scalars(select(ChatProposal).where(ChatProposal.id.in_(ids), ChatProposal.user_id == user.id))}
        cards = [proposals.card(db, found[i], user) for i in ids if i in found]
    return {
        "id": str(row.id),
        "role": "user" if row.role == ChatRole.USER else "assistant",
        "text": display.get("text", ""),
        "choices": display.get("choices", []),
        "cards": cards,
        "paused": bool(display.get("paused")),
        "feedback": row.feedback,
        "created_at": row.created_at.isoformat(),
    }


def transcript(db: Session, user: User, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.display.is_not(None))
        .order_by(ChatMessage.seq)
    ).all()
    return [display_item(db, user, r) for r in rows]
