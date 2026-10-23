"""Connecting a customer's AI assistant: discovery, registration, the consent page,
tokens (PKCE, rotation, reuse), revocation and personal access tokens."""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.core.timeutils import freeze_time
from app.models import AgentToken, AuditLog, Notification
from app.models.enums import NotificationChannel, NotificationType, Role
from tests.agent_helpers import REDIRECT, Agent, approve, authorize_params, connect, exchange, pkce, register
from tests.conftest import NOW, auth_headers


@pytest.fixture
def customer(client, user_headers):
    """A customer with the assistant subscription."""
    assert client.post("/api/chat/subscription", headers=user_headers).json()["active"]
    return user_headers


# ---------------------------------------------------------------- discovery and registration


def test_an_app_finds_the_authorization_server_from_the_mcp_endpoint(client) -> None:
    response = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
    challenge = response.headers["WWW-Authenticate"]
    assert challenge.startswith("Bearer resource_metadata=") and "/.well-known/oauth-protected-resource/api/mcp" in challenge

    resource = client.get("/.well-known/oauth-protected-resource/api/mcp").json()
    assert resource["resource"].endswith("/api/mcp") and resource["bearer_methods_supported"] == ["header"]
    server = client.get("/.well-known/oauth-authorization-server").json()
    assert server["issuer"] == resource["authorization_servers"][0]
    assert server["authorization_endpoint"].endswith("/oauth/authorize")  # the website's own consent page
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert server["authorization_response_iss_parameter_supported"] is True


@pytest.mark.parametrize(
    "uris",
    [["http://evil.example/callback"], ["https://claude.ai/cb#frag"], [], ["not a url"], [f"https://a.io/{i}" for i in range(6)]],
)
def test_registration_only_takes_https_or_loopback_redirects(client, uris) -> None:
    response = client.post("/api/oauth/register", json={"client_name": "X", "redirect_uris": uris})
    assert response.status_code == 400 and response.json()["error"] == "invalid_redirect_uri"


def test_desktop_apps_may_use_a_loopback_redirect_and_get_a_secret_when_they_ask(client) -> None:
    app = register(client, redirect_uris=["http://127.0.0.1:54545/callback"], token_endpoint_auth_method="client_secret_post")
    assert app["client_secret"].startswith("bk_cs_") and app["token_endpoint_auth_method"] == "client_secret_post"
    assert "client_secret" not in register(client)


# ---------------------------------------------------------------- the consent page


def test_the_consent_page_shows_the_app_where_it_returns_and_what_it_asks(client, customer) -> None:
    app = register(client, client_name="Claude")
    _, challenge = pkce()
    info = client.get("/api/oauth/authorize/info", params=authorize_params(app["client_id"], challenge), headers=customer).json()
    assert info["app"] == "Claude" and info["return_host"] == "claude.ai"
    assert {s["scope"] for s in info["scopes"]} == {"catalog:read", "bookings:read", "bookings:write"}
    assert info["signed_in_recently"] is True and info["default_spend_limit"] == "100"


@pytest.mark.parametrize(
    "change, code",
    [
        ({"redirect_uri": "https://evil.example/cb"}, "OAUTH_BAD_REDIRECT"),
        ({"code_challenge_method": "plain"}, "OAUTH_BAD_REQUEST"),
        ({"scope": "admin:everything"}, "OAUTH_BAD_SCOPE"),
        ({"resource": "https://other.example/mcp"}, "OAUTH_BAD_RESOURCE"),
        ({"client_id": "00000000-0000-0000-0000-000000000000"}, "OAUTH_UNKNOWN_CLIENT"),
    ],
)
def test_a_bad_request_is_shown_on_the_page_never_sent_to_a_redirect(client, customer, change, code) -> None:
    app = register(client)
    _, challenge = pkce()
    params = {**authorize_params(app["client_id"], challenge), **change}
    response = client.get("/api/oauth/authorize/info", params=params, headers=customer)
    assert response.status_code == 400 and response.json()["code"] == code


def test_only_subscribed_customers_connect_apps(client, make, user_headers) -> None:
    app = register(client)
    _, challenge = pkce()
    params = authorize_params(app["client_id"], challenge)
    assert client.get("/api/oauth/authorize/info", params=params).status_code == 401
    assert client.get("/api/oauth/authorize/info", params=params, headers=user_headers).status_code == 402
    staff = auth_headers(client, make.user("admin@test.io", Role.ADMIN).email)
    assert client.get("/api/oauth/authorize/info", params=params, headers=staff).status_code == 403


def test_connecting_needs_a_recent_sign_in(client, customer) -> None:
    app = register(client)
    _, challenge = pkce()
    freeze_time(NOW + timedelta(minutes=11))
    response = client.post("/api/oauth/authorize", headers=customer, json={**authorize_params(app["client_id"], challenge), "approve": True})
    assert response.status_code == 403 and response.json()["code"] == "SIGN_IN_AGAIN"


def test_denying_sends_the_app_access_denied(client, customer) -> None:
    app = register(client)
    _, challenge = pkce()
    response = client.post("/api/oauth/authorize", headers=customer, json={**authorize_params(app["client_id"], challenge), "approve": False})
    query = parse_qs(urlparse(response.json()["redirect"]).query)
    assert response.json()["redirect"].startswith(REDIRECT) and query["error"] == ["access_denied"] and query["state"] == ["xyz"]


def test_connecting_tells_the_customer_by_e_mail(client, db, customer) -> None:
    connect(client, customer)
    notices = db.scalars(select(Notification).where(Notification.type == NotificationType.AGENT_CONNECTED)).all()
    assert {n.channel for n in notices} == {NotificationChannel.IN_APP, NotificationChannel.EMAIL}
    assert "Not you? Disconnect it" in notices[0].body
    assert db.scalar(select(AuditLog).where(AuditLog.action == "AGENT_CONNECTED")) is not None


# ---------------------------------------------------------------- tokens


def test_the_code_works_once_with_the_right_verifier(client, db, customer) -> None:
    app = register(client)
    verifier, challenge = pkce()
    code = approve(client, customer, app["client_id"], challenge)
    assert exchange(client, app["client_id"], code, "x" * 50).json()["error"] == "invalid_grant"  # wrong PKCE verifier

    code = approve(client, customer, app["client_id"], challenge)
    tokens = exchange(client, app["client_id"], code, verifier).json()
    assert tokens["access_token"].startswith("bk_at_") and tokens["refresh_token"].startswith("bk_rt_")
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert set(tokens["scope"].split()) == {"catalog:read", "bookings:read", "bookings:write"}
    assert Agent(client, tokens["access_token"]).rpc("ping").json()["result"] == {}

    # The same code again is a replay: everything issued from it stops working.
    replay = exchange(client, app["client_id"], code, verifier)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert Agent(client, tokens["access_token"]).rpc("ping").status_code == 401
    # Only hashes are stored.
    assert db.scalar(select(AgentToken).where(AgentToken.token_hash == tokens["access_token"])) is None


def test_codes_expire_and_are_bound_to_the_redirect(client, customer) -> None:
    app = register(client)
    verifier, challenge = pkce()
    code = approve(client, customer, app["client_id"], challenge)
    assert exchange(client, app["client_id"], code, verifier, redirect_uri="https://claude.ai/other").json()["error"] == "invalid_grant"
    code = approve(client, customer, app["client_id"], challenge)
    freeze_time(NOW + timedelta(minutes=6))
    assert exchange(client, app["client_id"], code, verifier).json()["error"] == "invalid_grant"


def test_refresh_tokens_rotate_and_a_reused_one_ends_the_connection(client, customer) -> None:
    tokens = connect(client, customer)
    fresh = client.post("/api/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": tokens["client_id"]})
    assert fresh.status_code == 200 and fresh.json()["refresh_token"] != tokens["refresh_token"]
    assert Agent(client, fresh.json()["access_token"]).rpc("ping").status_code == 200

    reused = client.post("/api/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": tokens["client_id"]})
    assert reused.json()["error"] == "invalid_grant"
    assert Agent(client, fresh.json()["access_token"]).rpc("ping").status_code == 401


def test_access_tokens_expire(client, customer) -> None:
    tokens = connect(client, customer)
    freeze_time(NOW + timedelta(minutes=61))
    response = Agent(client, tokens["access_token"]).rpc("ping")
    assert response.status_code == 401 and 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_a_confidential_client_must_send_its_secret(client, customer) -> None:
    app = register(client, token_endpoint_auth_method="client_secret_basic")
    verifier, challenge = pkce()
    code = approve(client, customer, app["client_id"], challenge)
    assert exchange(client, app["client_id"], code, verifier).status_code == 401
    code = approve(client, customer, app["client_id"], challenge)
    response = client.post(
        "/api/oauth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier},
        auth=(app["client_id"], app["client_secret"]),
    )
    assert response.status_code == 200, response.text


def test_revoking_a_refresh_token_ends_its_access_tokens(client, customer) -> None:
    tokens = connect(client, customer)
    assert client.post("/api/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": tokens["client_id"]}).status_code == 200
    assert Agent(client, tokens["access_token"]).rpc("ping").status_code == 401


# ---------------------------------------------------------------- the customer's control


def test_the_customer_sees_and_disconnects_their_apps(client, customer) -> None:
    tokens = connect(client, customer, weekly_spend_limit="250")
    [app] = client.get("/api/connections", headers=customer).json()["connections"]
    assert app["name"] == "Claude" and app["return_host"] == "claude.ai" and app["allow_changes"] is True
    assert app["weekly_spend_limit"] == "250.00" and app["spent_this_week"] == "0.00"

    changed = client.put(f"/api/connections/{app['id']}", headers=customer, json={"allow_changes": False, "weekly_spend_limit": "50"}).json()
    assert changed["allow_changes"] is False and changed["weekly_spend_limit"] == "50.00"
    assert client.put(f"/api/connections/{app['id']}", headers=customer, json={"allow_changes": True}).json()["code"] == "APPROVE_AGAIN"
    assert client.put(f"/api/connections/{app['id']}", headers=customer, json={"weekly_spend_limit": "77"}).status_code == 422

    assert client.delete(f"/api/connections/{app['id']}", headers=customer).status_code == 200
    assert client.get("/api/connections", headers=customer).json()["connections"] == []
    assert Agent(client, tokens["access_token"]).rpc("ping").status_code == 401


def test_approving_the_same_app_again_replaces_its_tokens(client, customer) -> None:
    first = connect(client, customer)
    app_id = first["client_id"]
    verifier, challenge = pkce()
    code = approve(client, customer, app_id, challenge)
    second = exchange(client, app_id, code, verifier).json()
    assert Agent(client, first["access_token"]).rpc("ping").status_code == 401
    assert Agent(client, second["access_token"]).rpc("ping").status_code == 200
    assert len(client.get("/api/connections", headers=customer).json()["connections"]) == 1


def test_resetting_the_password_disconnects_every_app(client, db, user, customer) -> None:
    from app.core.security import hash_token, new_token
    from app.models import PasswordResetToken

    tokens = connect(client, customer)
    raw = new_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_token(raw), expires_at=NOW + timedelta(minutes=30)))
    db.commit()
    assert client.post("/api/auth/reset-password", json={"token": raw, "password": "a new password 123"}).status_code == 200
    assert Agent(client, tokens["access_token"]).rpc("ping").status_code == 401


def test_personal_tokens_are_read_only_unless_changes_are_allowed(client, customer) -> None:
    made = client.post("/api/connections/tokens", headers=customer, json={"name": "Siri Shortcut", "days": 90}).json()
    assert made["token"].startswith("bk_pat_") and made["mcp_url"].endswith("/api/mcp")
    assert made["connection"]["kind"] == "TOKEN" and made["connection"]["expires_at"] is not None
    reader = Agent(client, made["token"])
    assert "hold_booking" not in reader.tools() and "list_my_bookings" in reader.tools()
    refused = reader.call("hold_booking", service="x", date="2027-03-02", time="10:00")
    assert refused["_is_error"] and refused["error"] in ("UNKNOWN_TOOL", "NOT_ALLOWED")

    writer = client.post("/api/connections/tokens", headers=customer, json={"name": "Muse", "days": 30, "allow_changes": True}).json()
    assert "hold_booking" in Agent(client, writer["token"]).tools()
    assert client.post("/api/connections/tokens", headers=customer, json={"name": "Forever", "days": 365}).status_code == 422


def test_a_token_needs_a_recent_sign_in(client, customer) -> None:
    freeze_time(NOW + timedelta(minutes=11))
    response = client.post("/api/connections/tokens", headers=customer, json={"name": "Siri", "days": 30})
    assert response.status_code == 403 and response.json()["code"] == "SIGN_IN_AGAIN"


def test_an_account_that_becomes_staff_loses_its_assistants(client, db, user, customer) -> None:
    tokens = connect(client, customer)
    user.role = Role.STAFF
    db.commit()
    assert Agent(client, tokens["access_token"]).rpc("ping").status_code == 401
