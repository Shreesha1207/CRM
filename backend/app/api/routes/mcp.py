"""The MCP endpoint customers' AI assistants call: POST /api/mcp with a bearer token.

Only a connection's own token gets in, and only to the tools its scopes allow. See
app/services/agents/mcp.py for the protocol and app/services/agents/tools.py for the tools.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.api.deps import DB
from app.core.config import get_settings
from app.services.agents import mcp, oauth

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _unauthorized(error: str | None = None) -> JSONResponse:
    challenge = f'Bearer resource_metadata="{oauth.issuer()}/.well-known/oauth-protected-resource/api/mcp"'
    if error:
        challenge += f', error="{error}"'
    return JSONResponse(
        {"error": error or "unauthorized", "error_description": "Connect this app to your account first"},
        status_code=401,
        headers={"WWW-Authenticate": challenge},
    )


def _origin_allowed(request: Request) -> bool:
    """Browsers say where a request comes from; only our own pages may call from one
    (MCP's guard against DNS rebinding). Servers and apps send no Origin."""
    origin = request.headers.get("origin")
    return origin is None or origin.rstrip("/") in {o.rstrip("/") for o in [*get_settings().cors_origins, oauth.issuer()]}


async def _body(request: Request) -> bytes:
    return await request.body()


@router.post("")
def call(request: Request, body: Annotated[bytes, Depends(_body)], db: DB) -> Response:
    if not _origin_allowed(request):
        return JSONResponse({"error": "forbidden_origin"}, status_code=403)
    header = request.headers.get("authorization", "")
    raw = header[7:].strip() if header.lower().startswith("bearer ") else None
    found = oauth.access(db, raw)
    if found is None:
        return _unauthorized("invalid_token" if raw else None)
    connection, token = found
    if token.resource and token.resource.rstrip("/") not in (oauth.mcp_url(), oauth.issuer()):
        return _unauthorized("invalid_token")
    connection_id = connection.id
    db.close()
    try:
        message: Any = json.loads(body)
    except ValueError:
        return JSONResponse(mcp.rpc_error(None, mcp.PARSE_ERROR, "The body is not valid JSON"), status_code=400)
    # The 2026-07-28 transport repeats the method and tool in headers; they must match the body.
    method_header, name_header = request.headers.get("mcp-method"), request.headers.get("mcp-name")
    if isinstance(message, dict) and (
        (method_header and method_header != message.get("method")) or (name_header and name_header != mcp.name_of(message))
    ):
        return JSONResponse(mcp.rpc_error(message.get("id"), mcp.INVALID_REQUEST, "Mcp-Method / Mcp-Name do not match the body"), status_code=400)
    answer = mcp.handle(message, connection_id)
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(answer)


@router.get("")
@router.delete("")
def no_stream() -> Response:
    """No server-sent stream and no sessions to end: every request stands on its own."""
    return Response(status_code=405, headers={"Allow": "POST"})
