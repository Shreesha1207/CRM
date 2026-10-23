"""The MCP server, as a pure function: one JSON-RPC request in, one response out.

Stateless Streamable HTTP with JSON responses: no session, no server-sent stream.
Clients of the 2026-07-28 revision send every request on its own (`server/discover`
when they want our capabilities); older ones start with `initialize`, which is
answered the same way. Only tools are offered.
"""

import json
import uuid
from typing import Any

from app.db.session import SessionLocal
from app.models import AgentConnection
from app.services.agents import tools

# Newest first; a client asking for another gets the newest.
PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")
SERVER_NAME = "booking-crm"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _version(params: dict[str, Any]) -> str:
    asked = params.get("protocolVersion") or (params.get("_meta") or {}).get("io.modelcontextprotocol/protocolVersion")
    return asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]


def _server(params: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        text = tools.instructions(db)
    return {
        "protocolVersion": _version(params),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "title": "Bookings", "version": "1.0.0"},
        "instructions": text,
    }


def handle(message: Any, connection_id: uuid.UUID) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; None for a notification (nothing to answer)."""
    if isinstance(message, list):
        return rpc_error(None, INVALID_REQUEST, "Batches are not supported: send one request at a time")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return rpc_error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Not a JSON-RPC 2.0 request")
    method, id_ = message["method"], message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return rpc_error(id_, INVALID_PARAMS, "params must be an object")
    if "id" not in message:
        return None  # notifications/initialized, notifications/cancelled...
    if method in ("initialize", "server/discover"):
        return _result(id_, _server(params))
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        with SessionLocal() as db:
            scopes = db.get(AgentConnection, connection_id).scopes
        return _result(id_, {"tools": tools.definitions(scopes)})
    if method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return rpc_error(id_, INVALID_PARAMS, "tools/call needs a name and an arguments object")
        call = tools.run(connection_id, name, arguments)
        return _result(
            id_,
            {
                "content": [{"type": "text", "text": json.dumps(call.result, separators=(",", ":"), default=str)}],
                "structuredContent": json.loads(json.dumps(call.result, default=str)),
                "isError": call.is_error,
            },
        )
    return rpc_error(id_, METHOD_NOT_FOUND, f"Method not found: {method}")


def name_of(message: Any) -> str | None:
    """What a request is about, as the Mcp-Name header says it: the tool's name."""
    if isinstance(message, dict) and isinstance(message.get("params"), dict):
        name = message["params"].get("name")
        return name if isinstance(name, str) else None
    return None
