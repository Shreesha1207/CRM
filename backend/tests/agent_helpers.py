"""Connecting a customer's AI assistant in tests, the way Claude or ChatGPT does it."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
MCP = "/api/mcp"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client: TestClient, **metadata) -> dict:
    body = {"client_name": "Claude", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none", **metadata}
    response = client.post("/api/oauth/register", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def authorize_params(client_id: str, challenge: str, **extra) -> dict:
    return {
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "catalog:read bookings:read bookings:write",
        "state": "xyz",
        **extra,
    }


def approve(client: TestClient, headers: dict, client_id: str, challenge: str, **choices) -> str:
    """The customer presses Allow; returns the code from the redirect."""
    body = {**authorize_params(client_id, challenge), "approve": True, **choices}
    response = client.post("/api/oauth/authorize", headers=headers, json=body)
    assert response.status_code == 200, response.text
    query = parse_qs(urlparse(response.json()["redirect"]).query)
    assert query["state"] == ["xyz"]
    return query["code"][0]


def exchange(client: TestClient, client_id: str, code: str, verifier: str, **extra):
    return client.post(
        "/api/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier, "client_id": client_id, **extra},
    )


def connect(client: TestClient, headers: dict, **choices) -> dict:
    """The whole OAuth dance; returns the token response (plus client_id)."""
    app = register(client)
    verifier, challenge = pkce()
    code = approve(client, headers, app["client_id"], challenge, **choices)
    response = exchange(client, app["client_id"], code, verifier)
    assert response.status_code == 200, response.text
    return {**response.json(), "client_id": app["client_id"]}


class Agent:
    """An MCP client holding one access token."""

    def __init__(self, client: TestClient, token: str) -> None:
        self.client, self.token, self.ids = client, token, 0

    def rpc(self, method: str, params: dict | None = None, **headers):
        self.ids += 1
        return self.client.post(
            MCP,
            json={"jsonrpc": "2.0", "id": self.ids, "method": method, **({"params": params} if params is not None else {})},
            headers={"Authorization": f"Bearer {self.token}", **headers},
        )

    def tools(self) -> list[str]:
        return [t["name"] for t in self.rpc("tools/list").json()["result"]["tools"]]

    def call(self, name: str, **arguments) -> dict:
        response = self.rpc("tools/call", {"name": name, "arguments": arguments})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        return {**result["structuredContent"], "_is_error": result["isError"]}
