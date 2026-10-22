"""Admin: the problems customers sent from the booking assistant.

These are the only customer chats staff can read: a chat becomes readable when its
customer sends it to the admin, and every opening is written to the audit log.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB
from app.api.deps import require as require_permission
from app.core.errors import NotFound
from app.core.permissions import Permission
from app.core.timeutils import utcnow
from app.jobs.runner import dispatch_notifications
from app.models import ChatHelpRequest, User
from app.models.enums import ChatHelpStatus
from app.services import audit
from app.services.chat import help, tools

router = APIRouter(prefix="/api/admin/help-requests", tags=["admin: help requests"])

Reviewer = Depends(require_permission(Permission.CHAT_REVIEW))


class ResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _out(db, request: ChatHelpRequest, now: datetime) -> dict[str, Any]:
    customer = request.user
    resolver = db.get(User, request.resolved_by_id) if request.resolved_by_id else None
    return {
        "id": str(request.id),
        "conversation_id": str(request.conversation_id),
        "status": request.status.value,
        "summary": request.summary,
        "created_at": request.created_at.isoformat(),
        "due_at": request.due_at.isoformat(),
        "overdue": request.status == ChatHelpStatus.OPEN and request.due_at <= now,
        "customer": {"id": str(customer.id), "name": customer.name, "email": customer.email, "phone": customer.phone},
        "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
        "resolved_by": resolver.name if resolver else None,
        "resolution_note": request.resolution_note,
    }


def _get(db, request_id: uuid.UUID) -> ChatHelpRequest:
    request = db.get(ChatHelpRequest, request_id)
    if request is None:
        raise NotFound("Help request")
    return request


@router.get("")
def list_requests(db: DB, _: User = Reviewer, status: Literal["open", "resolved"] = "open") -> list[dict[str, Any]]:
    """Open ones by when they are due (overdue first); resolved ones newest first."""
    stmt = select(ChatHelpRequest).where(ChatHelpRequest.status == ChatHelpStatus(status.upper()))
    stmt = stmt.order_by(ChatHelpRequest.due_at if status == "open" else ChatHelpRequest.resolved_at.desc())
    now = utcnow()
    return [_out(db, r, now) for r in db.scalars(stmt.limit(100))]


@router.get("/count")
def count_open(db: DB, _: User = Reviewer) -> dict[str, int]:
    now = utcnow()
    open_ = select(func.count()).select_from(ChatHelpRequest).where(ChatHelpRequest.status == ChatHelpStatus.OPEN)
    return {"open": db.scalar(open_), "overdue": db.scalar(open_.where(ChatHelpRequest.due_at <= now))}


@router.get("/{request_id}")
def get_request(request_id: uuid.UUID, db: DB, admin: User = Reviewer) -> dict[str, Any]:
    """The problem, the customer's details and upcoming bookings, and the whole chat:
    as it was when they sent it, and anything after."""
    request = _get(db, request_id)
    audit.record(db, actor_id=admin.id, action="CHAT_VIEWED", entity_type="chat_help_request", entity_id=request.id)
    db.commit()
    sent = {item["id"] for item in request.transcript}
    since = [item for item in help.chat_history(db, request.user, request.conversation_id) if item["id"] not in sent]
    bookings = tools.list_my_bookings(db, request.user, None, tools.ListBookingsIn(limit=10))["bookings"]
    return {**_out(db, request, utcnow()), "transcript": request.transcript, "since": since, "bookings": bookings}


@router.post("/{request_id}/resolve")
def resolve_request(request_id: uuid.UUID, body: ResolveIn, db: DB, background: BackgroundTasks, admin: User = Reviewer) -> dict[str, Any]:
    request = _get(db, request_id)
    audit.record(
        db, actor_id=admin.id, action="CHAT_HELP_RESOLVED", entity_type="chat_help_request", entity_id=request.id,
        new={"note": (body.note or "").strip() or None},
    )  # fmt: skip
    help.resolve(db, request, admin, body.note)
    background.add_task(dispatch_notifications)
    return _out(db, request, utcnow())
