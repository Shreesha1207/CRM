"""The customer's Connected apps: the AI assistants that can use their account,
what each did, its spending limit, and personal access tokens."""

import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, rate_limit
from app.api.routes.chat import Customer, Subscriber
from app.core.errors import AppError, NotFound
from app.core.timeutils import utcnow
from app.models import AgentConnection, AgentToken, ChatMessage, User
from app.models.enums import AgentConnectionKind, AgentTokenKind, ChatRole
from app.services import settings_service
from app.services.agents import oauth, tools

router = APIRouter(prefix="/api/connections", tags=["connections"])

ACTIVITY = 15


def _own(db, user: User, connection_id: uuid.UUID) -> AgentConnection:
    connection = db.get(AgentConnection, connection_id)
    if connection is None or connection.user_id != user.id:
        raise NotFound("Connection")
    return connection


def _out(db, connection: AgentConnection) -> dict[str, Any]:
    now = utcnow()
    conversation = oauth.conversation_of(db, connection)
    activity = (
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id, ChatMessage.role == ChatRole.NOTICE, ChatMessage.display.is_not(None))
            .order_by(ChatMessage.seq.desc())
            .limit(ACTIVITY)
        ).all()
        if conversation is not None
        else []
    )
    expires = db.scalar(
        select(AgentToken.expires_at)
        .where(AgentToken.connection_id == connection.id, AgentToken.kind == AgentTokenKind.ACCESS, AgentToken.revoked_at.is_(None))
        .order_by(AgentToken.expires_at.desc())
        .limit(1)
    )
    redirect = connection.client.redirect_uris[0] if connection.client and connection.client.redirect_uris else None
    return {
        "id": str(connection.id),
        "kind": connection.kind.value,
        "name": connection.name,
        "return_host": urlparse(redirect).hostname if redirect else None,
        "scopes": connection.scopes,
        "allow_changes": oauth.BOOKINGS_WRITE in connection.scopes,
        "weekly_spend_limit": f"{connection.weekly_spend_limit:.2f}" if connection.weekly_spend_limit is not None else None,
        "spent_this_week": f"{tools.spent_this_week(db, connection, now):.2f}",
        "created_at": connection.created_at.isoformat(),
        "last_used_at": connection.last_used_at.isoformat() if connection.last_used_at else None,
        # Personal tokens: when the token stops working.
        "expires_at": expires.isoformat() if expires and connection.kind == AgentConnectionKind.TOKEN else None,
        "revoked_at": connection.revoked_at.isoformat() if connection.revoked_at else None,
        "activity": [{"text": row.display.get("text", ""), "at": row.created_at.isoformat()} for row in activity],
    }


@router.get("")
def list_connections(user: Customer, db: DB) -> dict[str, Any]:
    rows = db.scalars(
        select(AgentConnection).where(AgentConnection.user_id == user.id).order_by(AgentConnection.created_at.desc()).limit(50)
    ).unique().all()
    return {
        "mcp_url": oauth.mcp_url(),
        "currency": settings_service.get_rules(db).currency,
        "spend_limits": [str(v) if v is not None else None for v in oauth.SPEND_LIMITS],
        "token_days": list(oauth.TOKEN_DAYS),
        "connections": [_out(db, c) for c in rows if c.revoked_at is None],
        "disconnected": [_out(db, c) for c in rows if c.revoked_at is not None][:10],
    }


class ConnectionIn(BaseModel):
    weekly_spend_limit: Decimal | None = None
    # Only turning changes off: turning them on again needs a new approval.
    allow_changes: bool | None = None


def _limit(value: Decimal | None) -> Decimal | None:
    if value not in oauth.SPEND_LIMITS:
        raise AppError("INVALID_SPEND_LIMIT", "Choose one of the weekly spending limits", status_code=422)
    return value


@router.put("/{connection_id}")
def update_connection(connection_id: uuid.UUID, body: ConnectionIn, user: Customer, db: DB) -> dict[str, Any]:
    connection = _own(db, user, connection_id)
    if connection.revoked_at is not None:
        raise AppError("DISCONNECTED", "This app is disconnected", status_code=409)
    if "weekly_spend_limit" in body.model_fields_set:
        connection.weekly_spend_limit = _limit(body.weekly_spend_limit)
    if body.allow_changes is True and oauth.BOOKINGS_WRITE not in connection.scopes:
        raise AppError("APPROVE_AGAIN", "To let it make changes again, connect the app again (or make a new token).", status_code=409)
    if body.allow_changes is False:
        connection.scopes = [s for s in connection.scopes if s != oauth.BOOKINGS_WRITE]
    db.commit()
    return _out(db, connection)


@router.delete("/{connection_id}")
def disconnect(connection_id: uuid.UUID, user: Customer, db: DB) -> dict[str, Any]:
    connection = _own(db, user, connection_id)
    if connection.revoked_at is None:
        oauth.revoke(db, connection, reason="disconnected by the customer", actor_id=user.id)
    return _out(db, connection)


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    allow_changes: bool = False
    days: int = 90
    weekly_spend_limit: Decimal | None = oauth.DEFAULT_SPEND_LIMIT


@router.post("/tokens", status_code=201, dependencies=[Depends(rate_limit("agent-token", 10, 3600))])
def create_token(body: TokenIn, request: Request, user: Subscriber, db: DB) -> dict[str, Any]:
    """A personal access token, shown once. Only its hash is kept."""
    if not oauth.signed_in_recently(db, request):
        raise AppError("SIGN_IN_AGAIN", "For your security, sign in again to make an access token.", status_code=403)
    connection, token = oauth.create_personal_token(
        db, user, name=" ".join(body.name.split()), allow_changes=body.allow_changes, days=body.days, weekly_limit=_limit(body.weekly_spend_limit)
    )
    return {"token": token, "mcp_url": oauth.mcp_url(), "connection": _out(db, connection)}
