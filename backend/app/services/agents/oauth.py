"""How a customer's AI assistant gets access: OAuth 2.1 for apps (Claude, ChatGPT,
Gemini...) and personal access tokens (a Siri Shortcut, Meta Muse, a script).

    the app registers itself            (dynamic client registration)
    -> the customer signs in on our own consent page and approves it
    -> the app swaps the one-time code for tokens, proving it with PKCE
    -> it calls /api/mcp with the access token; refresh tokens rotate

The app never sees the customer's password. Every credential is random and only its
SHA-256 hash is stored; using a code or refresh token twice revokes the connection.
A connection only ever acts as its customer, within its scopes, and stops working
the moment it is revoked.
"""

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.permissions import is_staff
from app.core.security import constant_time_equals, hash_token
from app.core.timeutils import utcnow
from app.models import AgentClient, AgentConnection, AgentToken, AuthSession, ChatConversation, ChatProposal, User
from app.models.enums import (
    AgentConnectionKind,
    AgentTokenKind,
    ChatProposalKind,
    ChatProposalStatus,
    NotificationType,
    UserStatus,
)
from app.services import audit, settings_service
from app.services.notifications import _clock, notify

K = AgentTokenKind

CATALOG_READ, BOOKINGS_READ, BOOKINGS_WRITE = "catalog:read", "bookings:read", "bookings:write"
SCOPES = {
    CATALOG_READ: "See the services, people, places and free times",
    BOOKINGS_READ: "See your bookings, your profile and your notifications",
    BOOKINGS_WRITE: "Hold, move and cancel bookings and contact the admin for you, after asking you",
}
READ_ONLY = [CATALOG_READ, BOOKINGS_READ]

# The weekly spending limits a customer can choose (None = no limit).
SPEND_LIMITS: list[Decimal | None] = [Decimal("50"), Decimal("100"), Decimal("250"), Decimal("500"), None]
DEFAULT_SPEND_LIMIT = Decimal("100")
TOKEN_DAYS = (30, 90)
# Connecting an app or making a token needs a sign-in this recent.
FRESH_SIGN_IN_MINUTES = 10
CODE_MINUTES = 5
MAX_CONNECTIONS = 10
MAX_REDIRECT_URIS = 5
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")


class OAuthError(Exception):
    """An OAuth error, answered as {"error": ..., "error_description": ...} (RFC 6749)."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error, self.description, self.status_code = error, description, status_code


def mcp_url() -> str:
    return f"{get_settings().site_url}/api/mcp"


def issuer() -> str:
    return get_settings().site_url


def _secret(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


# ---------------------------------------------------------------- registration


def _redirect_ok(uri: Any) -> bool:
    if not isinstance(uri, str) or len(uri) > 500:
        return False
    url = urlparse(uri)
    if url.fragment or not url.netloc:
        return False
    if url.scheme == "https":
        return True
    # Desktop apps listen on their own machine.
    return url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")


def register_client(db: Session, metadata: dict[str, Any]) -> dict[str, Any]:
    """RFC 7591. Only https (or loopback) redirect URIs; a secret only when the app asks for one."""
    uris = metadata.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= MAX_REDIRECT_URIS or not all(_redirect_ok(u) for u in uris):
        raise OAuthError(
            "invalid_redirect_uri", f"Give 1 to {MAX_REDIRECT_URIS} redirect URIs, each https (or http on localhost), without a fragment"
        )
    name = metadata.get("client_name") or "AI assistant"
    if not isinstance(name, str) or not name.strip():
        raise OAuthError("invalid_client_metadata", "client_name must be text")
    method = metadata.get("token_endpoint_auth_method") or "client_secret_basic"  # RFC 7591's default
    if method not in AUTH_METHODS:
        raise OAuthError("invalid_client_metadata", f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}")
    grants = metadata.get("grant_types") or ["authorization_code", "refresh_token"]
    if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"}:
        raise OAuthError("invalid_client_metadata", "Only the authorization_code and refresh_token grants are supported")
    if not set(metadata.get("response_types") or ["code"]) <= {"code"}:
        raise OAuthError("invalid_client_metadata", "Only the code response type is supported")
    secret = _secret("bk_cs_") if method != "none" else None
    client = AgentClient(
        id=uuid.uuid4(),
        client_name=" ".join(name.split())[:100],
        redirect_uris=list(dict.fromkeys(uris)),
        token_endpoint_auth_method=method,
        client_secret_hash=hash_token(secret) if secret else None,
        created_at=utcnow(),
    )
    db.add(client)
    db.commit()
    out = {
        "client_id": str(client.id),
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
    }
    if secret:
        out.update(client_secret=secret, client_secret_expires_at=0)
    return out


def find_client(db: Session, client_id: str | None) -> AgentClient | None:
    try:
        return db.get(AgentClient, uuid.UUID(str(client_id)))
    except ValueError:
        return None


def authenticate_client(db: Session, client_id: str | None, secret: str | None) -> AgentClient:
    client = find_client(db, client_id)
    if client is None:
        raise OAuthError("invalid_client", "Unknown client", 401)
    if client.client_secret_hash is not None:
        if not secret or not constant_time_equals(hash_token(secret), client.client_secret_hash):
            raise OAuthError("invalid_client", "Client authentication failed", 401)
    return client


# ---------------------------------------------------------------- the consent page


@dataclass
class AuthorizeRequest:
    client: AgentClient
    redirect_uri: str
    scopes: list[str]
    state: str | None
    code_challenge: str
    resource: str | None

    @property
    def return_host(self) -> str:
        return urlparse(self.redirect_uri).hostname or self.redirect_uri


def check_authorize(db: Session, params: dict[str, Any]) -> AuthorizeRequest:
    """What the app asks for. Problems are shown on the consent page and never sent to a
    redirect URI that might not be the app's."""
    client = find_client(db, params.get("client_id"))
    if client is None:
        raise AppError("OAUTH_UNKNOWN_CLIENT", "This app is not registered. Add the connector again from the app.", status_code=400)
    redirect_uri = params.get("redirect_uri") or (client.redirect_uris[0] if len(client.redirect_uris) == 1 else None)
    if redirect_uri not in client.redirect_uris:
        raise AppError("OAUTH_BAD_REDIRECT", "This app asked to return to an address it did not register.", status_code=400)
    if params.get("response_type") != "code":
        raise AppError("OAUTH_BAD_REQUEST", "Only response_type=code is supported.", status_code=400)
    challenge = params.get("code_challenge") or ""
    if params.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
        raise AppError("OAUTH_BAD_REQUEST", "This app must use PKCE with S256.", status_code=400)
    requested = (params.get("scope") or " ".join(SCOPES)).split()
    if not requested or not set(requested) <= set(SCOPES):
        raise AppError("OAUTH_BAD_SCOPE", f"Unknown scope. Known: {', '.join(SCOPES)}.", status_code=400)
    resource = params.get("resource") or None
    if resource is not None and resource.rstrip("/") not in (mcp_url(), issuer()):
        raise AppError("OAUTH_BAD_RESOURCE", "This app asked for access to a different server.", status_code=400)
    state = params.get("state")
    return AuthorizeRequest(client, redirect_uri, list(dict.fromkeys(requested)), state[:500] if state else None, challenge, resource)


def signed_in_recently(db: Session, request: Request) -> bool:
    session_id = getattr(request.state, "session_id", None)
    session = db.get(AuthSession, session_id) if session_id else None
    return session is not None and session.created_at >= utcnow() - timedelta(minutes=FRESH_SIGN_IN_MINUTES)


def _scopes(requested: list[str], allow_changes: bool) -> list[str]:
    granted = [s for s in requested if s != BOOKINGS_WRITE]
    granted += [s for s in READ_ONLY if s not in granted]
    return granted + [BOOKINGS_WRITE] if allow_changes and BOOKINGS_WRITE in requested else granted


def _active_connections(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count()).select_from(AgentConnection).where(AgentConnection.user_id == user.id, AgentConnection.revoked_at.is_(None))
    )


def _new_connection(
    db: Session, user: User, *, kind: AgentConnectionKind, name: str, scopes: list[str], limit: Decimal | None, client: AgentClient | None
) -> AgentConnection:
    if _active_connections(db, user) >= MAX_CONNECTIONS:
        raise AppError("TOO_MANY_CONNECTIONS", f"You can connect at most {MAX_CONNECTIONS} apps. Disconnect one first.", status_code=409)
    now = utcnow()
    connection = AgentConnection(
        id=uuid.uuid4(), user_id=user.id, client_id=client.id if client else None, kind=kind, name=name,
        scopes=scopes, weekly_spend_limit=limit, created_at=now,
    )  # fmt: skip
    db.add(connection)
    db.flush()
    # Its holds, offered changes and help requests live in a conversation of its own.
    db.add(
        ChatConversation(
            user_id=user.id, title=f"{name} (connected app)", agent_connection_id=connection.id, created_at=now, updated_at=now
        )
    )
    return connection


def _tell_customer(db: Session, user: User, connection: AgentConnection, what: str) -> None:
    business = settings_service.get_rules(db).business_name
    at = utcnow()
    audit.record(
        db, actor_id=user.id, action="AGENT_TOKEN_CREATED" if connection.kind == AgentConnectionKind.TOKEN else "AGENT_CONNECTED",
        entity_type="agent_connection", entity_id=connection.id, new={"name": connection.name, "scopes": connection.scopes},
    )  # fmt: skip
    notify(
        db,
        user_id=user.id,
        type=NotificationType.AGENT_CONNECTED,
        title=f"{connection.name} was connected to your account",
        body=(
            f"{what} at {business} on {at:%a %d %b %Y}, {_clock(at)} UTC.\n"
            "Not you? Disconnect it under Connected apps and change your password."
        ),
        link="/connections",
    )


def approve(db: Session, user: User, req: AuthorizeRequest, *, allow_changes: bool, weekly_limit: Decimal | None) -> str:
    """The customer approved the app: a one-time code for it, at its redirect URI."""
    scopes = _scopes(req.scopes, allow_changes)
    connection = db.scalar(
        select(AgentConnection)
        .where(AgentConnection.user_id == user.id, AgentConnection.client_id == req.client.id, AgentConnection.revoked_at.is_(None))
        .with_for_update(of=AgentConnection)
    )
    if connection is None:
        connection = _new_connection(
            db, user, kind=AgentConnectionKind.OAUTH, name=req.client.client_name, scopes=scopes, limit=weekly_limit, client=req.client
        )
    else:
        # Approved again (e.g. the app lost its tokens): the old tokens stop working.
        _revoke_tokens(db, connection)
        connection.scopes, connection.weekly_spend_limit = scopes, weekly_limit
    code = _secret("bk_ac_")
    now = utcnow()
    db.add(
        AgentToken(
            connection_id=connection.id, kind=K.CODE, token_hash=hash_token(code), resource=req.resource, created_at=now,
            expires_at=now + timedelta(minutes=CODE_MINUTES),
            payload={"redirect_uri": req.redirect_uri, "code_challenge": req.code_challenge, "client_id": str(req.client.id)},
        )
    )  # fmt: skip
    _tell_customer(db, user, connection, f"You allowed {connection.name} to use your account")
    db.commit()
    return _redirect(req, code=code)


def deny(req: AuthorizeRequest) -> str:
    return _redirect(req, error="access_denied", error_description="The customer did not allow access")


def _redirect(req: AuthorizeRequest, **params: str) -> str:
    query = {**params, **({"state": req.state} if req.state else {}), "iss": issuer()}
    return f"{req.redirect_uri}{'&' if '?' in req.redirect_uri else '?'}{urlencode(query)}"


# ---------------------------------------------------------------- tokens


def _issue(db: Session, connection: AgentConnection, resource: str | None) -> dict[str, Any]:
    settings = get_settings()
    now = utcnow()
    access, refresh = _secret("bk_at_"), _secret("bk_rt_")
    db.add(
        AgentToken(
            connection_id=connection.id, kind=K.ACCESS, token_hash=hash_token(access), resource=resource, created_at=now,
            expires_at=now + timedelta(minutes=settings.agent_access_token_minutes),
        )
    )  # fmt: skip
    db.add(
        AgentToken(
            connection_id=connection.id, kind=K.REFRESH, token_hash=hash_token(refresh), resource=resource, created_at=now,
            expires_at=now + timedelta(days=settings.agent_refresh_token_days),
        )
    )  # fmt: skip
    db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": settings.agent_access_token_minutes * 60,
        "refresh_token": refresh,
        "scope": " ".join(connection.scopes),
    }


def _locked_token(db: Session, raw: str | None, kind: AgentTokenKind) -> AgentToken | None:
    if not raw:
        return None
    return db.scalar(select(AgentToken).where(AgentToken.token_hash == hash_token(raw), AgentToken.kind == kind).with_for_update())


def _pkce(verifier: str | None, challenge: str) -> bool:
    if not verifier or not 43 <= len(verifier) <= 128:
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return constant_time_equals(digest, challenge)


def exchange_code(
    db: Session, client: AgentClient, code: str | None, redirect_uri: str | None, verifier: str | None, resource: str | None
) -> dict[str, Any]:
    token = _locked_token(db, code, K.CODE)
    connection = db.get(AgentConnection, token.connection_id) if token else None
    if token is None or connection is None or token.payload.get("client_id") != str(client.id):
        raise OAuthError("invalid_grant", "The authorization code is not valid")
    if token.used_at is not None:
        # A code used twice was stolen or replayed: nothing issued from it may keep working.
        revoke(db, connection, reason="authorization code reused")
        raise OAuthError("invalid_grant", "The authorization code was already used")
    token.used_at = utcnow()
    if connection.revoked_at is not None or token.revoked_at is not None or token.expires_at <= utcnow():
        db.commit()
        raise OAuthError("invalid_grant", "The authorization code has expired")
    if redirect_uri != token.payload["redirect_uri"]:
        db.commit()
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
    if not _pkce(verifier, token.payload["code_challenge"]):
        db.commit()
        raise OAuthError("invalid_grant", "PKCE verification failed")
    if resource and token.resource and resource.rstrip("/") != token.resource.rstrip("/"):
        db.commit()
        raise OAuthError("invalid_target", "resource does not match the authorization request")
    return _issue(db, connection, resource or token.resource)


def refresh(db: Session, client: AgentClient, raw: str | None) -> dict[str, Any]:
    token = _locked_token(db, raw, K.REFRESH)
    connection = db.get(AgentConnection, token.connection_id) if token else None
    if token is None or connection is None or connection.client_id != client.id or connection.revoked_at is not None:
        raise OAuthError("invalid_grant", "The refresh token is not valid")
    if token.used_at is not None or token.revoked_at is not None:
        # Refresh tokens rotate, so an old one coming back means it was copied.
        revoke(db, connection, reason="refresh token reused")
        raise OAuthError("invalid_grant", "The refresh token was already used")
    if token.expires_at <= utcnow():
        raise OAuthError("invalid_grant", "The refresh token has expired")
    token.used_at = utcnow()
    return _issue(db, connection, token.resource)


def revoke_token(db: Session, client: AgentClient, raw: str | None) -> None:
    """RFC 7009: revoking a refresh token also ends the access tokens of its connection."""
    if not raw:
        return
    token = db.scalar(select(AgentToken).where(AgentToken.token_hash == hash_token(raw)))
    connection = db.get(AgentConnection, token.connection_id) if token else None
    if token is None or connection is None or connection.client_id != client.id:
        return  # unknown tokens are not an error (RFC 7009)
    now = utcnow()
    token.revoked_at = token.revoked_at or now
    if token.kind == K.REFRESH:
        db.execute(
            update(AgentToken)
            .where(AgentToken.connection_id == connection.id, AgentToken.kind == K.ACCESS, AgentToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    db.commit()


def access(db: Session, raw: str | None) -> tuple[AgentConnection, AgentToken] | None:
    """The connection an access (or personal) token belongs to, if it still works."""
    if not raw:
        return None
    token = db.scalar(select(AgentToken).where(AgentToken.token_hash == hash_token(raw), AgentToken.kind == K.ACCESS))
    if token is None or token.revoked_at is not None or token.expires_at <= utcnow():
        return None
    connection = db.get(AgentConnection, token.connection_id)
    if connection is None or connection.revoked_at is not None:
        return None
    user = db.get(User, connection.user_id)
    # Customers only: an account that has since become staff keeps no assistant.
    if user is None or user.status != UserStatus.ACTIVE or is_staff(user.role):
        return None
    return connection, token


# ---------------------------------------------------------------- personal tokens and revoking


def create_personal_token(
    db: Session, user: User, *, name: str, allow_changes: bool, days: int, weekly_limit: Decimal | None
) -> tuple[AgentConnection, str]:
    """A token for an assistant that can't do OAuth. Shown once; read-only unless asked."""
    if days not in TOKEN_DAYS:
        raise AppError("INVALID_EXPIRY", f"A token lasts {' or '.join(map(str, TOKEN_DAYS))} days", status_code=422)
    scopes = READ_ONLY + ([BOOKINGS_WRITE] if allow_changes else [])
    connection = _new_connection(db, user, kind=AgentConnectionKind.TOKEN, name=name, scopes=scopes, limit=weekly_limit, client=None)
    raw = _secret("bk_pat_")
    now = utcnow()
    db.add(
        AgentToken(
            connection_id=connection.id, kind=K.ACCESS, token_hash=hash_token(raw), created_at=now,
            expires_at=now + timedelta(days=days),
        )
    )  # fmt: skip
    _tell_customer(db, user, connection, f"You made the access token {name!r}")
    db.commit()
    return connection, raw


def _revoke_tokens(db: Session, connection: AgentConnection) -> None:
    db.execute(
        update(AgentToken).where(AgentToken.connection_id == connection.id, AgentToken.revoked_at.is_(None)).values(revoked_at=utcnow())
    )


def conversation_of(db: Session, connection: AgentConnection) -> ChatConversation:
    return db.scalar(select(ChatConversation).where(ChatConversation.agent_connection_id == connection.id))


def revoke(db: Session, connection: AgentConnection, *, reason: str, actor_id: uuid.UUID | None = None) -> None:
    """Disconnect it: every token stops working, and what it still holds is released. Commits."""
    from app.services.chat import proposals

    now = utcnow()
    connection.revoked_at = connection.revoked_at or now
    _revoke_tokens(db, connection)
    audit.record(
        db, actor_id=actor_id, action="AGENT_DISCONNECTED", entity_type="agent_connection", entity_id=connection.id,
        new={"name": connection.name, "reason": reason},
    )  # fmt: skip
    db.commit()
    conversation = conversation_of(db, connection)
    user = db.get(User, connection.user_id)
    if conversation is None or user is None:
        return
    pending = db.scalars(
        select(ChatProposal.turn_id)
        .where(ChatProposal.conversation_id == conversation.id, ChatProposal.status == ChatProposalStatus.PENDING)
        .distinct()
    ).all()
    for turn_id in pending:
        proposals.withdraw_turn(db, turn_id, user)


def revoke_all(db: Session, user_id: uuid.UUID, *, reason: str) -> None:
    for connection in db.scalars(
        select(AgentConnection).where(AgentConnection.user_id == user_id, AgentConnection.revoked_at.is_(None))
    ).all():
        revoke(db, connection, reason=reason, actor_id=user_id)


def open_holds(db: Session, connection: AgentConnection, now: datetime) -> list[ChatProposal]:
    conversation = conversation_of(db, connection)
    if conversation is None:
        return []
    return list(
        db.scalars(
            select(ChatProposal)
            .where(
                ChatProposal.conversation_id == conversation.id,
                ChatProposal.status == ChatProposalStatus.PENDING,
                ChatProposal.expires_at > now,
            )
            .order_by(ChatProposal.created_at)
        )
    )


def is_hold(proposal: ChatProposal) -> bool:
    return proposal.kind == ChatProposalKind.HOLD
