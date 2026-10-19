"""One turn of the booking assistant: from the customer's message to the reply they see.

    quick checks (rules, Prompt Guard) -> policy guard, while the model reads the message
    -> model <-> tools, a few rounds -> check the reply (guards)
    -> show it, or throw the whole turn away and show the fixed reply

Nothing the model answers is stored or acted on before the policy guard allows the message.

Every message the model sees is stored in the OpenAI chat format and sent again
on later turns (the most recent turns only). A turn that is blocked is kept for
review but never sent to the model again, and any booking it held is released.
"""

import contextvars
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
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
from app.services.chat import groq, prompt, proposals, tools
from app.services.chat.groq import RateLimited, ToolCallFailed
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
        "appointment, or questions about our services. If you have a problem with a booking or a payment, "
        "tell me what happened and I can pass it to the admin."
    )


UNAVAILABLE = "The assistant is unavailable right now. Please try again in a moment, or use the booking page."
# Added to the request (not stored) when the model's last tool call could not be read.
TOOL_CALL_HINT = (
    "Your last tool call could not be read. Call only the tools you were given, with arguments as valid JSON "
    "that match their schema (ids are UUIDs from earlier tool results, dates are YYYY-MM-DD), or answer in plain text."
)
MAX_TOOL_CALL_RETRIES = 2
# The latest turns are replayed with their tool calls and results (the ids and times
# the customer may be answering); older turns as text only, to save tokens.
FULL_TURNS = 2
TOO_COMPLEX = "Sorry, I couldn't finish that. Could you try again, one step at a time? You can also use the booking page."
REFUSED = "Sorry, I can't help with that. I can find a time, book, move or cancel an appointment, or answer questions about our services."
BUSY = "The assistant is busy right now. Please try again in a minute, or use the booking page."
OUT_FOR_TODAY = "The assistant has reached today's limit. Please use the booking page, or try again tomorrow."


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
    """The messages sent to the model: the last few customer turns, never cut between
    a tool call and its result."""
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.blocked_reason.is_(None),
            ChatMessage.role != ChatRole.NOTICE,
        )
        .order_by(ChatMessage.seq)
    ).all()
    turns: list[list[ChatMessage]] = []
    for row in rows:
        if row.role == ChatRole.USER or not turns:
            turns.append([])
        turns[-1].append(row)
    turns = turns[-get_settings().chat_history_turns:]
    messages: list[dict[str, Any]] = []
    for n, turn in enumerate(turns):
        recent = n >= len(turns) - FULL_TURNS
        for row in turn:
            if recent or _is_conversation(row):
                messages.append(row.content)
    return messages


def _is_conversation(row: ChatMessage) -> bool:
    """What the customer and the assistant said, without the tool calls and results between."""
    if row.role == ChatRole.USER:
        return True
    return row.role == ChatRole.ASSISTANT and not row.content.get("tool_calls") and bool(row.content.get("content"))


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
class TurnTiming:
    """Where a turn's time went: logged as one line per turn."""

    started: float = field(default_factory=time.perf_counter)
    steps: list[str] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)

    @contextmanager
    def step(self, label: str) -> Iterator[list[str]]:
        """Times the block; anything appended to the yielded list is shown after it."""
        started = time.perf_counter()
        notes: list[str] = []
        try:
            yield notes
        finally:
            ms = (time.perf_counter() - started) * 1000
            self.steps.append(f"{label} {ms:.0f} ms" + (f" ({', '.join(notes)})" if notes else ""))

    def summary(self) -> str:
        total = (time.perf_counter() - self.started) * 1000
        waited = f"; waited {sum(self.waits):.0f} s for rate limits" if self.waits else ""
        return f"Chat turn {total:.0f} ms: " + ", ".join(self.steps) + waited


@dataclass
class _Turn:
    db: Session
    conversation: ChatConversation
    user: User
    turn_id: uuid.UUID
    timing: TurnTiming = field(default_factory=TurnTiming)

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
        waits = groq.rate_limit_waits.set(turn.timing.waits)
        try:
            _run(turn, text, model, guard, emit)
        except Exception:
            log.exception("Chat turn failed")
            db.rollback()
            turn.block("error")
            proposals.withdraw_turn(db, turn.turn_id, user)
            notice = turn.add(ChatRole.NOTICE, None, display={"text": UNAVAILABLE, "contact_admin": True})
            emit("reply", _reply(db, user, notice))
        finally:
            groq.rate_limit_waits.reset(waits)
            log.info(turn.timing.summary())
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
    # When the assistant itself failed, the customer may send their problem to the admin.
    offer_help = message in (TOO_COMPLEX, UNAVAILABLE) and not paused
    notice = turn.add(ChatRole.NOTICE, None, display={"text": message, "choices": choices, "paused": paused, "contact_admin": offer_help})
    emit("reply", _reply(db, turn.user, notice))


def _run(turn: _Turn, text: str, model: ChatModel, guard: GuardClient, emit: Emit) -> None:
    db, conversation = turn.db, turn.conversation
    now = utcnow()
    if conversation.title == "New chat":
        conversation.title = text[:80]
    conversation.updated_at = now
    db.commit()

    previous = last_reply(db, conversation.id)
    turn.add(ChatRole.USER, {"role": "user", "content": text}, display={"text": text})

    emit("status", {"label": "Reading your message…"})
    with turn.timing.step("guard in"):
        verdict: Verdict = guard.screen_injection(text)
    if not verdict.allowed:
        _block(turn, verdict, emit)
        return
    # The policy guard reads the message while the model does: nothing the model answers
    # is stored or acted on until the guard has allowed the message.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        policy = pool.submit(contextvars.copy_context().run, _screen_policy, guard, turn.timing, text, previous)
        _converse(turn, model, guard, emit, policy)
    finally:
        pool.shutdown(wait=False)


def _screen_policy(guard: GuardClient, timing: TurnTiming, text: str, previous: str | None) -> Verdict:
    with timing.step("guard policy"):
        return guard.screen_policy(text, previous)


def _block(turn: _Turn, verdict: Verdict, emit: Emit) -> None:
    message = UNAVAILABLE if verdict.unavailable else fixed_reply(turn.db)
    _refuse(turn, verdict.stored_reason, message, emit, strike=not verdict.unavailable)


def _converse(turn: _Turn, model: ChatModel, guard: GuardClient, emit: Emit, policy: Future[Verdict] | None) -> None:
    db, user, conversation = turn.db, turn.user, turn.conversation
    settings = get_settings()
    now = utcnow()

    events = proposals.report(db, conversation.id, user, now)
    turn.add(ChatRole.SYSTEM, {"role": "system", "content": prompt.turn_note(db, user, now, events)})

    system = prompt.system_prompt(db)
    definitions = tools.definitions()
    ctx = tools.ToolContext(user_id=user.id, conversation_id=conversation.id, turn_id=turn.turn_id)
    final = None
    problem = ""
    rejected = 0
    last_stop = ""
    tool_errors: list[str] = []
    for _round in range(settings.chat_max_tool_rounds):
        emit("status", {"label": "Thinking…"})
        messages = _history(db, conversation.id)
        if problem:
            messages.append({"role": "system", "content": f"{TOOL_CALL_HINT} Problem: {problem}"})
        failure: Exception | None = None
        try:
            with turn.timing.step("model") as notes:
                result = model.run_turn(system=system, tools=definitions, messages=messages)
                notes.append(f"{result.input_tokens} in, {result.output_tokens} out tokens")
        except Exception as exc:
            failure = exc
        if policy is not None:
            # The first answer waits for the policy guard before anything is done with it.
            verdict = policy.result()
            policy = None
            if not verdict.allowed:
                _block(turn, verdict, emit)
                return
        if isinstance(failure, ToolCallFailed):
            # The model wrote a tool call the server couldn't read: ask again, with a hint.
            rejected += 1
            problem = str(failure)
            log.warning("Model tool call rejected (%s of %s): %s | %s", rejected, MAX_TOOL_CALL_RETRIES + 1, failure, failure.failed_generation)
            if rejected > MAX_TOOL_CALL_RETRIES:
                _refuse(turn, "tool call failed", TOO_COMPLEX, emit, strike=False)
                return
            continue
        if isinstance(failure, RateLimited):
            _refuse(turn, "rate limited", BUSY if failure.retry_after <= 120 else OUT_FOR_TODAY, emit, strike=False)
            return
        if failure is not None:
            raise failure
        last_stop = result.stop_reason
        row = turn.add(ChatRole.ASSISTANT, result.message, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        conversation.input_tokens += result.input_tokens
        conversation.output_tokens += result.output_tokens
        db.commit()
        if result.stop_reason == "refusal":
            _refuse(turn, "refusal", REFUSED, emit, strike=False)
            return
        if result.stop_reason == "tool_use" and result.tool_calls:
            for call in result.tool_calls:
                spec = tools.TOOLS_BY_NAME.get(call["name"])
                if spec is not None and spec.status:
                    emit("status", {"label": spec.status})
                try:
                    args = json.loads(call["arguments"] or "{}")
                except ValueError:
                    content = json.dumps({"error": "INVALID_JSON", "message": "The tool arguments were not valid JSON."})
                    failed = True
                else:
                    with turn.timing.step(call["name"]):
                        content, failed = tools.run(call["name"], args, ctx)
                if failed:
                    tool_errors.append(f"{call['name']}: {content[:200]}")
                turn.add(ChatRole.TOOL, {"role": "tool", "tool_call_id": call["id"], "content": content})
            continue
        if result.stop_reason == "end_turn":
            final = row
            break
        log.warning("Chat turn stopped with %s", result.stop_reason)
        break
    if final is None:
        log.warning("Chat turn unfinished: last stop %r, tool errors %s", last_stop, tool_errors)
        _refuse(turn, "unfinished", TOO_COMPLEX, emit, strike=False)
        return

    reply_text = consistent_reply(db, user, ctx.proposal_ids, model_text(final.content))
    emit("status", {"label": "Checking the reply…"})
    with turn.timing.step("guard out"):
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


def model_text(message: dict[str, Any]) -> str:
    return (message.get("content") or "").strip()


_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s?([AP])M\b", re.IGNORECASE)


def _clocks(text: str) -> set[str]:
    return {f"{int(h)}:{m}{ap.upper()}" for h, m, ap in _CLOCK.findall(text)}


def _describe(card: dict[str, Any]) -> str:
    """The card in words, for when the model's own words disagree with it."""
    when, was = card["when"], card.get("was")
    where = f"{when['date']}, {when['time']} ({when['zone']})"
    if card["kind"] == "HOLD":
        return f"I've held {card['service']} with {card['resource']} for you on {where}. Press Confirm on the card to book it, or Delete."
    if card["kind"] == "RESCHEDULE" and was:
        return (
            f"I can move your {card['service']} from {was['date']}, {was['time']} to {where}, with {card['resource']}. "
            "Press Confirm on the card to move it, or Decline to keep it as it is."
        )
    return f"I can cancel your {card['service']} on {where}. Press Confirm on the card to cancel it, or Decline to keep it."


def consistent_reply(db: Session, user: User, proposal_ids: list[uuid.UUID], text: str) -> str:
    """The cards are what gets booked, so a reply that names a time shown on none of them
    ("11:00 AM – 1:00 PM" over a card for 11:00 AM – 12:00 PM) is replaced by the cards in words.
    Markdown the model adds despite being told not to is removed."""
    text = text.replace("**", "")
    if not proposal_ids:
        return text
    found = db.scalars(select(ChatProposal).where(ChatProposal.id.in_(proposal_ids)).order_by(ChatProposal.created_at)).all()
    cards = [c for c in (proposals.card(db, p, user) for p in found) if "when" in c]
    shown = set().union(*(_clocks(f"{c['when']['time']} {(c.get('was') or {}).get('time', '')}") for c in cards)) if cards else set()
    if cards and _clocks(text) - shown:
        log.warning("Reply named times %s but the cards show %s; using the cards' words", sorted(_clocks(text) - shown), sorted(shown))
        return " ".join(_describe(c) for c in cards)
    return text


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
        "contact_admin": bool(display.get("contact_admin")),
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
