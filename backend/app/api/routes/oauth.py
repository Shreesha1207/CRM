"""OAuth 2.1 for customers' AI assistants: discovery, registration, the consent page's
API, tokens and revocation. See app/services/agents/oauth.py."""

import base64
import json
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import DB, rate_limit
from app.api.routes.chat import Subscriber
from app.core.errors import AppError
from app.models import AgentClient
from app.services import settings_service
from app.services.agents import oauth

router = APIRouter(tags=["oauth"])


def _oauth_error(exc: oauth.OAuthError) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Basic realm="oauth"'} if exc.status_code == 401 else None
    return JSONResponse({"error": exc.error, "error_description": exc.description}, status_code=exc.status_code, headers=headers)


# ---------------------------------------------------------------- discovery


def _resource_metadata() -> dict[str, Any]:
    return {
        "resource": oauth.mcp_url(),
        "authorization_servers": [oauth.issuer()],
        "scopes_supported": list(oauth.SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "Bookings",
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/api/mcp")
def protected_resource() -> dict[str, Any]:
    """RFC 9728: where the MCP endpoint's tokens come from."""
    return _resource_metadata()


@router.get("/.well-known/oauth-authorization-server")
def authorization_server() -> dict[str, Any]:
    """RFC 8414."""
    site = oauth.issuer()
    return {
        "issuer": site,
        # The consent page is the website's own, where the customer signs in.
        "authorization_endpoint": f"{site}/oauth/authorize",
        "token_endpoint": f"{site}/api/oauth/token",
        "registration_endpoint": f"{site}/api/oauth/register",
        "revocation_endpoint": f"{site}/api/oauth/revoke",
        "scopes_supported": list(oauth.SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": list(oauth.AUTH_METHODS),
        "revocation_endpoint_auth_methods_supported": list(oauth.AUTH_METHODS),
        "authorization_response_iss_parameter_supported": True,
    }


# ---------------------------------------------------------------- registration


# Per IP: one AI service registers (and refreshes) for many customers from few addresses,
# so these are generous; codes and tokens are 256-bit random, so they can't be guessed anyway.
@router.post("/api/oauth/register", status_code=201, dependencies=[Depends(rate_limit("oauth-register", 300, 3600))])
async def register(request: Request, db: DB) -> Any:
    try:
        metadata = await request.json()
    except ValueError:
        metadata = None
    if not isinstance(metadata, dict):
        return _oauth_error(oauth.OAuthError("invalid_client_metadata", "Send the client metadata as a JSON object"))
    try:
        return JSONResponse(oauth.register_client(db, metadata), status_code=201)
    except oauth.OAuthError as exc:
        return _oauth_error(exc)


# ---------------------------------------------------------------- the consent page


class AuthorizeIn(BaseModel):
    client_id: str
    redirect_uri: str | None = None
    response_type: str
    code_challenge: str
    code_challenge_method: str
    scope: str | None = None
    state: str | None = Field(default=None, max_length=500)
    resource: str | None = None
    approve: bool
    allow_changes: bool = True
    # One of oauth.SPEND_LIMITS; null = no limit.
    weekly_spend_limit: Decimal | None = oauth.DEFAULT_SPEND_LIMIT


def _limit(value: Decimal | None) -> Decimal | None:
    if value not in oauth.SPEND_LIMITS:
        raise AppError("INVALID_SPEND_LIMIT", "Choose one of the weekly spending limits", status_code=422)
    return value


@router.get("/api/oauth/authorize/info")
def authorize_info(request: Request, user: Subscriber, db: DB) -> dict[str, Any]:
    """What the consent page shows: the app, where it returns to, and what it asks for."""
    req = oauth.check_authorize(db, dict(request.query_params))
    rules = settings_service.get_rules(db)
    return {
        "app": req.client.client_name,
        "return_host": req.return_host,
        "business": rules.business_name,
        "currency": rules.currency,
        "scopes": [{"scope": s, "description": oauth.SCOPES[s]} for s in req.scopes],
        "asks_for_changes": oauth.BOOKINGS_WRITE in req.scopes,
        "spend_limits": [str(v) if v is not None else None for v in oauth.SPEND_LIMITS],
        "default_spend_limit": str(oauth.DEFAULT_SPEND_LIMIT),
        "signed_in_recently": oauth.signed_in_recently(db, request),
    }


@router.post("/api/oauth/authorize", dependencies=[Depends(rate_limit("oauth-authorize", 20, 900))])
def authorize(body: AuthorizeIn, request: Request, user: Subscriber, db: DB) -> dict[str, str]:
    req = oauth.check_authorize(db, body.model_dump())
    if not body.approve:
        return {"redirect": oauth.deny(req)}
    if not oauth.signed_in_recently(db, request):
        raise AppError("SIGN_IN_AGAIN", "For your security, sign in again to connect an app.", status_code=403)
    redirect = oauth.approve(db, user, req, allow_changes=body.allow_changes, weekly_limit=_limit(body.weekly_spend_limit))
    return {"redirect": redirect}


# ---------------------------------------------------------------- tokens


def _client_credentials(request: Request, client_id: str | None, client_secret: str | None) -> tuple[str | None, str | None]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("basic "):
        try:
            user, _, password = base64.b64decode(header[6:]).decode().partition(":")
            return unquote(user), unquote(password)
        except (ValueError, UnicodeDecodeError):
            return None, None
    return client_id, client_secret


async def _form(request: Request) -> dict[str, str]:
    """The request's form fields (application/x-www-form-urlencoded, as OAuth sends them;
    JSON is accepted too)."""
    body = await request.body()
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return {}
        return {k: str(v) for k, v in data.items() if isinstance(v, str | int)} if isinstance(data, dict) else {}
    return {k: v[0] for k, v in parse_qs(body.decode(errors="replace"), keep_blank_values=False).items()}


Fields = Annotated[dict[str, str], Depends(_form)]


@router.post("/api/oauth/token", dependencies=[Depends(rate_limit("oauth-token", 600, 60))])
def token(request: Request, form: Fields, db: DB) -> JSONResponse:
    try:
        client: AgentClient = oauth.authenticate_client(db, *_client_credentials(request, form.get("client_id"), form.get("client_secret")))
        grant = form.get("grant_type")
        if grant == "authorization_code":
            tokens = oauth.exchange_code(
                db, client, form.get("code"), form.get("redirect_uri"), form.get("code_verifier"), form.get("resource")
            )
        elif grant == "refresh_token":
            tokens = oauth.refresh(db, client, form.get("refresh_token"))
        else:
            raise oauth.OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token")
    except oauth.OAuthError as exc:
        return _oauth_error(exc)
    return JSONResponse(tokens, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/api/oauth/revoke", dependencies=[Depends(rate_limit("oauth-revoke", 600, 60))])
def revoke(request: Request, form: Fields, db: DB) -> JSONResponse:
    try:
        client = oauth.authenticate_client(db, *_client_credentials(request, form.get("client_id"), form.get("client_secret")))
    except oauth.OAuthError as exc:
        return _oauth_error(exc)
    oauth.revoke_token(db, client, form.get("token"))
    return JSONResponse({})
