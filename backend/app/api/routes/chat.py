"""The customers' booking assistant, behind a (demo) paywall."""

import json
import queue
import threading
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.errors import AppError, Forbidden, NotFound
from app.core.permissions import ROLE_PERMISSIONS
from app.core.timeutils import utcnow
from app.jobs.runner import dispatch_notifications
from app.models import ChatConversation, ChatMessage, ChatSubscription, User
from app.models.enums import ChatRole, ChatSubscriptionStatus
from app.schemas import (
    ChatConversationDetailOut,
    ChatConversationOut,
    ChatFeedbackIn,
    ChatHelpIn,
    ChatMessageIn,
    ChatProposalConfirmIn,
    ChatSubscriptionOut,
)
from app.services.chat import agent, help, proposals
from app.services.chat.guard import GuardClient, get_guard
from app.services.chat.llm import ChatModel, get_chat_model

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _customer(user: CurrentUser) -> User:
    if ROLE_PERMISSIONS.get(user.role):
        raise Forbidden("The booking assistant is for customers")
    return user


Customer = Annotated[User, Depends(_customer)]


def _subscription(db, user: User) -> ChatSubscription | None:
    return db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user.id))


def _subscriber(user: Customer, db: DB) -> User:
    sub = _subscription(db, user)
    if sub is None or sub.status != ChatSubscriptionStatus.ACTIVE:
        raise AppError("CHAT_SUBSCRIPTION_REQUIRED", "The booking assistant needs a subscription", status_code=402)
    return user


Subscriber = Annotated[User, Depends(_subscriber)]


def _subscription_out(sub: ChatSubscription | None) -> ChatSubscriptionOut:
    active = sub is not None and sub.status == ChatSubscriptionStatus.ACTIVE
    return ChatSubscriptionOut(
        active=active,
        activated_at=sub.activated_at if active else None,
        paused_until=sub.paused_until if active and sub.paused_until and sub.paused_until > utcnow() else None,
        configured=get_settings().chat_configured,
    )


# ---------------------------------------------------------------- the (demo) paywall


@router.get("/subscription", response_model=ChatSubscriptionOut)
def get_subscription(user: Customer, db: DB) -> ChatSubscriptionOut:
    return _subscription_out(_subscription(db, user))


@router.post("/subscription", response_model=ChatSubscriptionOut)
def subscribe(user: Customer, db: DB) -> ChatSubscriptionOut:
    """Demo checkout: no payment is taken. A payment provider's webhook would do this."""
    sub = _subscription(db, user)
    now = utcnow()
    if sub is None:
        sub = ChatSubscription(user_id=user.id, status=ChatSubscriptionStatus.ACTIVE, provider="demo", activated_at=now)
        db.add(sub)
    elif sub.status != ChatSubscriptionStatus.ACTIVE:
        sub.status, sub.activated_at, sub.cancelled_at = ChatSubscriptionStatus.ACTIVE, now, None
    db.commit()
    return _subscription_out(sub)


@router.delete("/subscription", response_model=ChatSubscriptionOut)
def unsubscribe(user: Customer, db: DB) -> ChatSubscriptionOut:
    sub = _subscription(db, user)
    if sub is not None and sub.status == ChatSubscriptionStatus.ACTIVE:
        sub.status, sub.cancelled_at = ChatSubscriptionStatus.CANCELLED, utcnow()
        db.commit()
    return _subscription_out(sub)


# ---------------------------------------------------------------- conversations


def _conversation(db, user: User, conversation_id: uuid.UUID) -> ChatConversation:
    conversation = db.get(ChatConversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise NotFound("Conversation")
    return conversation


@router.get("/conversations", response_model=list[ChatConversationOut])
def list_conversations(user: Subscriber, db: DB) -> list[ChatConversation]:
    return list(
        db.scalars(
            select(ChatConversation)
            .where(ChatConversation.user_id == user.id)
            .order_by(ChatConversation.updated_at.desc())
            .limit(50)
        )
    )


@router.post("/conversations", response_model=ChatConversationOut, status_code=201)
def new_conversation(user: Subscriber, db: DB) -> ChatConversation:
    now = utcnow()
    conversation = ChatConversation(user_id=user.id, title="New chat", created_at=now, updated_at=now)
    db.add(conversation)
    db.commit()
    return conversation


@router.get("/conversations/{conversation_id}", response_model=ChatConversationDetailOut)
def get_conversation(conversation_id: uuid.UUID, user: Subscriber, db: DB) -> ChatConversationDetailOut:
    conversation = _conversation(db, user, conversation_id)
    return ChatConversationDetailOut(
        id=conversation.id,
        title=conversation.title,
        updated_at=conversation.updated_at,
        items=agent.transcript(db, user, conversation.id),
        starters=agent.STARTERS,
        hold_minutes=get_settings().chat_hold_minutes,
    )


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: uuid.UUID,
    body: ChatMessageIn,
    user: Subscriber,
    db: DB,
    model: Annotated[ChatModel | None, Depends(get_chat_model)],
    guard: Annotated[GuardClient | None, Depends(get_guard)],
) -> StreamingResponse:
    """Runs one turn and streams its progress (`status` events) and the checked reply (`reply`)."""
    conversation = _conversation(db, user, conversation_id)
    if model is None or guard is None:
        raise AppError("CHAT_NOT_CONFIGURED", "The booking assistant is not set up yet", status_code=503)
    text = body.text.strip()
    if not text:
        raise AppError("EMPTY_MESSAGE", "Type a message", status_code=422)
    if len(text) > get_settings().chat_max_input_chars:
        raise AppError("MESSAGE_TOO_LONG", f"Please keep messages under {get_settings().chat_max_input_chars} characters", status_code=422)
    agent.check_limits(db, user, conversation)
    agent.take_lease(db, conversation)

    events: queue.Queue = queue.Queue()
    user_id = user.id

    def work() -> None:
        try:
            agent.run_turn(conversation_id, user_id, text, model, guard, lambda name, data: events.put((name, data)))
        finally:
            events.put(None)

    # The turn runs on its own thread, so it finishes (and saves) even if the browser goes away.
    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                yield _event("done", {})
                return
            yield _event(*item)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.post("/conversations/{conversation_id}/help", status_code=201)
def contact_admin(conversation_id: uuid.UUID, body: ChatHelpIn, user: Subscriber, db: DB, background: BackgroundTasks) -> dict:
    """After the assistant failed: the customer sends their own description of the problem,
    with this chat, to the admin. The confirmation is added to the chat."""
    conversation = _conversation(db, user, conversation_id)
    request = help.request_help(db, user, conversation, body.summary)
    background.add_task(dispatch_notifications)
    return {"id": str(request.id), "due_at": request.due_at.isoformat(), "message": help.confirmation(db, request)}


# ---------------------------------------------------------------- cards and feedback


@router.post("/proposals/{proposal_id}/confirm")
def confirm_proposal(
    proposal_id: uuid.UUID, user: Subscriber, db: DB, background: BackgroundTasks, body: ChatProposalConfirmIn | None = None
) -> dict:
    proposal = proposals.confirm(db, proposal_id, user, body.summary if body else None)
    background.add_task(dispatch_notifications)
    return proposals.card(db, proposal, user)


@router.post("/proposals/{proposal_id}/decline")
def decline_proposal(proposal_id: uuid.UUID, user: Subscriber, db: DB, background: BackgroundTasks) -> dict:
    proposal = proposals.decline(db, proposal_id, user)
    background.add_task(dispatch_notifications)
    return proposals.card(db, proposal, user)


@router.post("/messages/{message_id}/feedback")
def message_feedback(message_id: uuid.UUID, body: ChatFeedbackIn, user: Subscriber, db: DB) -> dict:
    row = db.get(ChatMessage, message_id)
    if row is None or row.user_id != user.id or row.display is None or row.role == ChatRole.USER:
        raise NotFound("Message")
    row.feedback = body.value
    db.commit()
    return {"id": str(row.id), "feedback": row.feedback}
