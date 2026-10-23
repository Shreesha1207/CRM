"""The MCP endpoint and its tools: the protocol, what each connection may do, booking in
two steps at the price the customer heard, the weekly spending limit, payment links,
and problems passed to the admin like the in-app assistant does."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.timeutils import freeze_time
from app.models import Booking, ChatConversation, ChatHelpRequest, Notification
from app.models.enums import BookingStatus, NotificationChannel, Role
from tests.agent_helpers import MCP, Agent, connect
from tests.conftest import NOW, auth_headers
from tests.test_chat import TUESDAY, gym  # noqa: F401  (gym is a fixture)

S = BookingStatus


@pytest.fixture
def customer(client, user_headers):
    assert client.post("/api/chat/subscription", headers=user_headers).json()["active"]
    return user_headers


@pytest.fixture
def agent(client, customer) -> Agent:
    return Agent(client, connect(client, customer)["access_token"])


def hold(agent: Agent, **extra) -> dict:
    return agent.call("hold_booking", service="strength coaching", person="Sam", date=TUESDAY.isoformat(), time="10:00", duration_minutes=120, **extra)


# ---------------------------------------------------------------- the protocol


def test_older_clients_initialize_and_newer_ones_discover(agent) -> None:
    started = agent.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-ai"}}).json()
    assert started["result"]["protocolVersion"] == "2025-06-18"
    assert started["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert "confirm_action" in started["result"]["instructions"]
    assert "Mcp-Session-Id" not in agent.rpc("ping").headers  # stateless

    discovered = agent.rpc("server/discover", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}).json()
    assert discovered["result"]["protocolVersion"] == "2026-07-28"
    assert agent.rpc("initialize", {"protocolVersion": "1999-01-01"}).json()["result"]["protocolVersion"] == "2026-07-28"


def test_transport_rules(client, agent) -> None:
    headers = {"Authorization": f"Bearer {agent.token}"}
    note = client.post(MCP, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    assert note.status_code == 202 and note.content == b""
    assert client.get(MCP, headers=headers).status_code == 405
    assert client.delete(MCP, headers=headers).status_code == 405
    assert client.post(MCP, json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers=headers).json()["error"]["code"] == -32600
    assert client.post(MCP, content=b"{not json", headers=headers).status_code == 400
    assert agent.rpc("resources/list").json()["error"]["code"] == -32601
    # The 2026-07-28 headers must say what the body says.
    mismatch = agent.rpc("tools/call", {"name": "get_my_profile", "arguments": {}}, **{"Mcp-Method": "tools/call", "Mcp-Name": "hold_booking"})
    assert mismatch.status_code == 400
    matching = agent.rpc("tools/call", {"name": "get_my_profile", "arguments": {}}, **{"Mcp-Method": "tools/call", "Mcp-Name": "get_my_profile"})
    assert matching.status_code == 200
    # Only our own pages may call it from a browser.
    assert agent.rpc("ping", Origin="https://evil.example").status_code == 403
    assert agent.rpc("ping", Origin="http://localhost:5173").status_code == 200


def test_twenty_tools_and_none_that_reach_the_database(agent) -> None:
    listed = agent.rpc("tools/list").json()["result"]["tools"]
    assert len(listed) == 20
    by_name = {t["name"]: t for t in listed}
    assert by_name["confirm_action"]["annotations"]["destructiveHint"] is True
    assert by_name["list_services"]["annotations"]["readOnlyHint"] is True
    assert by_name["hold_booking"]["annotations"]["readOnlyHint"] is False
    assert all(t["inputSchema"]["additionalProperties"] is False for t in listed)
    assert not any(word in json.dumps(listed).lower() for word in ("sql", "table", "query_database"))


def test_unknown_arguments_and_tools_are_refused(agent) -> None:
    assert agent.call("get_my_profile", sql="select * from users")["error"] == "INVALID_INPUT"
    assert agent.call("drop_tables")["error"] == "UNKNOWN_TOOL"


# ---------------------------------------------------------------- reading


def test_business_info_gives_the_date_and_policies(agent, gym) -> None:  # noqa: F811
    info = agent.call("get_business_info")
    assert info["now"]["date"] == "2027-02-26" and info["next_days"][4] == "Tue 2027-03-02"
    assert info["currency"] == "USD" and any("cancel" in p for p in info["policies"])


def test_catalogue_and_free_times(agent, gym) -> None:  # noqa: F811
    service, sam = gym
    detail = agent.call("get_service", service_id=str(service.id))
    assert detail["name"] == "Strength coaching" and detail["provided_by"][0]["name"] == "Coach Sam"
    person = agent.call("get_resource", resource_id=str(sam.id))
    assert person["timezone"] == "Asia/Kolkata" and person["weekly_hours"][0] == {"day": "Mon", "from": "09:00", "to": "17:00"}
    assert agent.call("list_locations")["locations"][0]["map"].startswith("https://www.google.com/maps")

    nxt = agent.call("find_next_available", service="strenght coaching", person="sam", count=3)
    assert nxt["service"] == "Strength coaching" and len(nxt["next_free"]) == 3
    assert nxt["next_free"][0]["label"].startswith("Fri 26 Feb")  # today, in Kolkata it is already 1:30 PM
    unclear = agent.call("find_next_available", service="pilates")
    assert unclear["needs"] == "service" and "ask the customer" in unclear["next_step"].lower()


# ---------------------------------------------------------------- booking in two steps


def test_a_hold_is_booked_only_when_confirmed_at_the_price_heard(client, db, agent, gym) -> None:  # noqa: F811
    held = hold(agent)
    [action] = held["actions"]
    assert action["kind"] == "new_booking" and action["price"] == "80.00" and action["currency"] == "USD"
    assert action["when"] == {"date": "Tue 02 Mar 2027", "time": "10:00 AM – 12:00 PM", "zone": "Asia/Kolkata"}
    assert "confirm_action" in held["next_step"] and "Press" not in json.dumps(held)
    booking_id = action["action_id"]
    assert db.scalars(select(Booking)).one().status == S.PENDING

    wrong = agent.call("confirm_action", action_id=booking_id, expected_price="40.00")
    assert wrong["error"] == "PRICE_MISMATCH" and "80.00" in wrong["message"]
    missing = agent.call("confirm_action", action_id=booking_id)
    assert missing["error"] == "PRICE_MISMATCH"

    done = agent.call("confirm_action", action_id=booking_id, expected_price="80")
    assert done["status"] == "booked" and not done["_is_error"]
    db.expire_all()
    assert db.scalars(select(Booking)).one().status == S.CONFIRMED
    assert db.scalar(select(Notification).where(Notification.channel == NotificationChannel.EMAIL, Notification.title == "Booking confirmed"))
    assert agent.call("confirm_action", action_id=booking_id, expected_price="80")["error"] == "ALREADY_ANSWERED"

    log = client.get("/api/connections", headers=auth_headers(client, "user@test.io")).json()["connections"][0]["activity"]
    assert [a["text"] for a in log][::-1] == [
        "Claude held Strength coaching with Coach Sam on Tue 02 Mar 2027, 10:00 AM – 12:00 PM (Asia/Kolkata), 80.00 USD.",
        "Claude booked Strength coaching with Coach Sam, Tue 02 Mar 2027, 10:00 AM – 12:00 PM (Asia/Kolkata).",
    ]


def test_discarding_releases_the_time(db, agent, gym) -> None:  # noqa: F811
    [action] = hold(agent)["actions"]
    assert agent.call("discard_action", action_id=action["action_id"])["status"] == "discarded"
    db.expire_all()
    assert db.scalars(select(Booking)).one().status == S.CANCELLED
    assert agent.call("list_pending_actions")["actions"] == []


def test_an_expired_hold_cannot_be_confirmed(agent, gym) -> None:  # noqa: F811
    [action] = hold(agent)["actions"]
    freeze_time(NOW + timedelta(minutes=11))
    assert agent.call("confirm_action", action_id=action["action_id"], expected_price="80.00")["error"] == "ALREADY_ANSWERED"


def test_moves_and_cancellations_change_nothing_until_confirmed(db, agent, gym) -> None:  # noqa: F811
    [held] = hold(agent)["actions"]
    agent.call("confirm_action", action_id=held["action_id"], expected_price="80.00")

    moved = agent.call("propose_reschedule", on_date=TUESDAY.isoformat(), query="strength", new_time="13:00")
    [move] = moved["actions"]
    assert move["kind"] == "move" and move["price"] == "80.00" and move["when"]["time"] == "1:00 PM – 3:00 PM"
    db.expire_all()
    assert db.scalars(select(Booking)).one().status == S.CONFIRMED  # nothing changed yet
    assert agent.call("confirm_action", action_id=move["action_id"], expected_price="80.00")["status"] == "moved"

    [cancel] = agent.call("propose_cancellation", query="strength coaching")["actions"]
    assert cancel["kind"] == "cancellation" and "price" not in cancel
    assert agent.call("confirm_action", action_id=cancel["action_id"])["status"] == "cancelled"
    db.expire_all()
    assert {b.status for b in db.scalars(select(Booking))} == {S.RESCHEDULED, S.CANCELLED}


def test_an_app_confirms_only_its_own_actions(client, customer, agent, gym) -> None:  # noqa: F811
    [action] = hold(agent)["actions"]
    other = Agent(client, client.post("/api/connections/tokens", headers=customer, json={"name": "Muse", "days": 30, "allow_changes": True}).json()["token"])
    assert other.call("confirm_action", action_id=action["action_id"], expected_price="80.00")["error"] == "NOT_FOUND"
    assert other.call("list_pending_actions")["actions"] == []


def test_the_weekly_spending_limit(client, db, customer, gym) -> None:  # noqa: F811
    agent = Agent(client, connect(client, customer, weekly_spend_limit="100")["access_token"])
    [first] = hold(agent)["actions"]
    assert agent.call("confirm_action", action_id=first["action_id"], expected_price="80.00")["status"] == "booked"
    [second] = agent.call("hold_booking", service="strength coaching", date=TUESDAY.isoformat(), time="14:00")["actions"]
    over = agent.call("confirm_action", action_id=second["action_id"], expected_price="40.00")
    assert over["error"] == "SPENDING_LIMIT" and "120.00" in over["message"]

    # Cancelled bookings don't count.
    [cancel] = agent.call("propose_cancellation", on_date=TUESDAY.isoformat(), around_time="10:00")["actions"]
    agent.call("confirm_action", action_id=cancel["action_id"])
    assert agent.call("confirm_action", action_id=second["action_id"], expected_price="40.00")["status"] == "booked"
    [app] = client.get("/api/connections", headers=customer).json()["connections"]
    assert app["spent_this_week"] == "40.00" and app["weekly_spend_limit"] == "100.00"


def test_with_online_payments_confirming_returns_a_payment_link(client, db, make, agent, gym) -> None:  # noqa: F811
    make.settings(online_payments=True)
    [action] = hold(agent)["actions"]
    done = agent.call("confirm_action", action_id=action["action_id"], expected_price="80.00")
    assert done["status"] == "awaiting_payment" and done["amount"] == "80.00"
    assert done["payment_url"].startswith("http") and "/pay/" in done["payment_url"]
    assert "Never ask for card" in done["next_step"]
    waiting = agent.call("list_pending_actions")["waiting_for_payment"]
    assert waiting[0]["payment_url"] == done["payment_url"]
    mine = agent.call("list_my_bookings")["bookings"][0]
    assert mine["waiting_for_payment"] is True and mine["payment"]["status"] == "pending"


# ---------------------------------------------------------------- the same guardrails as the chat


def test_a_problem_goes_to_the_admin_like_the_assistant_sends_it(client, db, make, agent, gym) -> None:  # noqa: F811
    make.user("admin@test.io", Role.ADMIN)
    [held] = hold(agent)["actions"]
    agent.call("confirm_action", action_id=held["action_id"], expected_price="80.00")
    vague = agent.call("contact_admin", summary="contact admin")
    assert vague["error"] == "NEEDS_DESCRIPTION"
    [message] = agent.call("contact_admin", summary="I was charged twice for my Tuesday strength coaching session.")["actions"]
    assert message["kind"] == "message_to_admin"
    assert db.scalar(select(ChatHelpRequest)) is None  # nothing sent before confirm_action

    sent = agent.call("confirm_action", action_id=message["action_id"])
    assert sent["status"] == "sent" and "within 6 hours" in sent["message"]
    request = db.scalar(select(ChatHelpRequest))
    assert request.summary == "I was charged twice for my Tuesday strength coaching session."
    texts = [item["text"] for item in request.transcript]
    assert texts[0].startswith("Claude held Strength coaching") and any("wrote down a problem" in t for t in texts)
    customer_note = db.scalar(select(Notification).where(Notification.title == "Your request was passed to the admin"))
    assert customer_note.link == "/connections"


def test_the_paywall_and_scopes_are_checked_on_every_call(client, customer, gym) -> None:  # noqa: F811
    agent = Agent(client, connect(client, customer, allow_changes=False)["access_token"])
    assert "hold_booking" not in agent.tools() and len(agent.tools()) == 14
    assert agent.call("hold_booking", service="x", date="2027-03-02", time="10:00")["error"] == "NOT_ALLOWED"

    client.delete("/api/chat/subscription", headers=customer)
    assert agent.call("get_my_profile")["error"] == "SUBSCRIPTION_REQUIRED"


def test_calls_are_rate_limited_per_connection(agent, settings) -> None:
    settings(agent_calls_per_minute=3)
    assert [agent.call("get_my_profile").get("error") for _ in range(4)] == [None, None, None, "RATE_LIMITED"]


def test_an_apps_conversation_is_not_a_chat(client, db, customer, agent) -> None:
    assert client.get("/api/chat/conversations", headers=customer).json() == []
    hidden = db.scalar(select(ChatConversation).where(ChatConversation.agent_connection_id.is_not(None)))
    assert client.get(f"/api/chat/conversations/{hidden.id}", headers=customer).status_code == 404


def test_disconnecting_releases_what_it_still_holds(client, db, customer, agent, gym) -> None:  # noqa: F811
    hold(agent)
    [app] = client.get("/api/connections", headers=customer).json()["connections"]
    client.delete(f"/api/connections/{app['id']}", headers=customer)
    db.expire_all()
    assert db.scalars(select(Booking)).one().status == S.CANCELLED
