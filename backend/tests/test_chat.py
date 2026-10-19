"""The booking assistant: the paywall, booking from a sentence (held, then confirmed or
deleted), offered changes, and what the model and the customer get to see."""

import json
import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.timeutils import freeze_time, utcnow
from app.models import Booking, ChatConversation, ChatMessage, ChatProposal, Notification
from app.models.enums import BookingStatus, ChatProposalStatus, ChatRole, NotificationChannel, Role
from app.services.chat import holds
from tests.chat_fakes import call, call_raw, last_tool_results, reply_of, say
from tests.conftest import MONDAY, NOW, at, auth_headers

TUESDAY = MONDAY + timedelta(days=1)


@pytest.fixture
def gym(db, make):
    """Strength coaching (60 minutes, bookable up to 3 hours) with Sam, in Kolkata."""
    kolkata = make.location("Asia/Kolkata", name="City Gym")
    kolkata.address = "12 MG Road, Kolkata"
    db.commit()
    service = make.service("Strength coaching", duration=60, max_duration_minutes=180, price=Decimal("40"))
    sam = make.resource("Coach Sam", services=[service], location=kolkata, hours=make.weekdays(time(9), time(17)))
    return service, sam


HELD_REPLY = "I've held Strength coaching with Coach Sam for you on Tue 02 Mar, 10:00 AM (Asia/Kolkata). Press Confirm to book it, or Delete."


def book(time_="10:00", duration=120, day=None, **extra):
    """The model's call for "Book strength coaching with Sam next Tuesday at 10 for two hours"."""
    args = {"service": "strength coaching", "person": "Sam", "date": (day or TUESDAY).isoformat(), "time": time_, **extra}
    if duration:
        args["duration_minutes"] = duration
    return call("book", args)


def hold_steps(service, resource, hour=10, duration=120):
    """What the model does with "Book strength coaching with Sam next Tuesday at 10 for two hours"."""
    return [book(f"{hour}:00", duration), say(HELD_REPLY)]


def emails(db) -> list[Notification]:
    db.expire_all()
    return list(db.scalars(select(Notification).where(Notification.channel == NotificationChannel.EMAIL)))


# ---------------------------------------------------------------- the paywall


def test_the_assistant_is_behind_a_paywall_for_customers_only(client, make, user_headers) -> None:
    assert client.get("/api/chat/subscription").status_code == 401
    staff = make.user("admin@test.io", Role.ADMIN)
    assert client.get("/api/chat/subscription", headers=auth_headers(client, staff.email)).status_code == 403

    assert client.get("/api/chat/subscription", headers=user_headers).json()["active"] is False
    assert client.post("/api/chat/conversations", headers=user_headers).status_code == 402
    assert client.get("/api/chat/conversations", headers=user_headers).status_code == 402

    paid = client.post("/api/chat/subscription", headers=user_headers).json()
    assert paid["active"] is True and paid["demo"] is True
    assert client.post("/api/chat/conversations", headers=user_headers).status_code == 201

    assert client.delete("/api/chat/subscription", headers=user_headers).json()["active"] is False
    assert client.post("/api/chat/conversations", headers=user_headers).status_code == 402


def test_without_model_and_guard_keys_the_assistant_says_it_is_not_set_up(client, user_headers) -> None:
    client.post("/api/chat/subscription", headers=user_headers)
    conversation = client.post("/api/chat/conversations", headers=user_headers).json()["id"]
    response = client.post(f"/api/chat/conversations/{conversation}/messages", headers=user_headers, json={"text": "hi"})
    assert response.status_code == 503
    assert response.json()["code"] == "CHAT_NOT_CONFIGURED"


# ---------------------------------------------------------------- booking from a sentence


def test_a_sentence_becomes_a_booking_held_for_the_customer(client, db, gym, chat) -> None:
    service, sam = gym
    reply = reply_of(chat.send("Book me strength coaching with Sam next Tuesday at 10 for two hours", *hold_steps(service, sam)))

    assert "Press Confirm" in reply["text"]
    [card] = reply["cards"]
    assert card["kind"] == "HOLD" and card["status"] == "PENDING"
    # The same details as the booking e-mails.
    assert card["service"] == "Strength coaching" and card["resource"] == "Coach Sam"
    assert card["when"] == {"date": "Tue 02 Mar 2027", "time": "10:00 AM – 12:00 PM", "zone": "Asia/Kolkata"}
    assert card["place"] == "City Gym, 12 MG Road, Kolkata"
    assert card["map_url"].startswith("https://www.google.com/maps")
    assert card["price"] == "80.00"  # two hours at 40.00 an hour

    booking = db.get(Booking, card["booking_id"])
    assert booking.status == BookingStatus.PENDING
    assert booking.start_datetime == datetime(2027, 3, 2, 4, 30, tzinfo=UTC)  # 10:00 in Kolkata
    assert booking.end_datetime - booking.start_datetime == timedelta(hours=2)
    assert emails(db) == []  # nothing is sent until the customer confirms

    # The time really is taken.
    other = auth_headers(client, _other(db).email)
    taken = client.post("/api/bookings", headers=other, json={"service_id": str(service.id), "resource_id": str(sam.id), "start": at(TUESDAY, 11)})
    assert taken.status_code == 409


def _other(db):
    from tests.conftest import Factory

    return Factory(db).user("other@test.io", name="Other Customer")


def test_one_call_resolves_the_names_and_holds_it(db, gym, chat) -> None:
    service, sam = gym
    reply_of(chat.send("Book me strength coaching with Sam next Tuesday at 10 for two hours", *hold_steps(service, sam)))
    first, second = chat.model.requests
    # The customer's words, then the server's note with the current time, as a system message.
    assert first["messages"][-2] == {"role": "user", "content": "Book me strength coaching with Sam next Tuesday at 10 for two hours"}
    assert first["messages"][-1]["role"] == "system"
    assert "Current time: Fri 26 Feb 2027" in first["messages"][-1]["content"]
    [held] = last_tool_results(second["messages"])
    assert held["held"] is True and held["booking"]["when"]["time"] == "10:00 AM – 12:00 PM"
    assert held["booking"]["service"] == "Strength coaching" and held["booking"]["resource"] == "Coach Sam"
    assert {t["function"]["name"] for t in first["tools"]} == {"book", "move", "cancel", "check_availability", "my_bookings", "offer_choices"}


def test_the_prompt_lists_the_catalogue_and_the_next_days(gym, chat, monkeypatch) -> None:
    from app.services.chat import tools

    service, sam = gym
    reply_of(chat.send("Hi", say("Hello!")))
    request = chat.model.requests[-1]
    # Every name that exists, with its id, so the model never has to guess one.
    assert "<catalogue>" in request["system"]
    assert '"name":"Strength coaching"' in request["system"] and str(service.id) not in request["system"]
    assert '"provided_by":["Coach Sam"]' in request["system"] and '"name":"Coach Sam","type":"person"' in request["system"]
    assert "Next days: Fri 26 Feb = 2027-02-26, Sat 27 Feb = 2027-02-27" in request["messages"][-1]["content"]

    # A catalogue too large for the prompt is left to the tools.
    monkeypatch.setattr(tools, "MAX_CATALOGUE_SERVICES", 0)
    reply_of(chat.send("Hi again", say("Hello!")))
    assert "<catalogue>" not in chat.model.requests[-1]["system"] and "pass names to the tools" in chat.model.requests[-1]["system"]


def test_each_turn_logs_where_its_time_went(gym, chat, caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="chat")
    reply_of(chat.send("Anything free Tuesday?", call("check_availability", {"date": TUESDAY.isoformat()}), say("Yes, Strength coaching.")))
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Chat turn")]
    assert "guard in" in line and "guard policy" in line and "guard out" in line and "check_availability" in line
    assert line.count("model ") == 2 and "100 in, 20 out tokens" in line


def test_confirm_books_it_and_sends_the_normal_confirmation(client, db, gym, chat) -> None:
    service, sam = gym
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]

    confirmed = chat.confirm(card["id"]).json()
    assert confirmed["status"] == "CONFIRMED" and confirmed["booking_status"] == "CONFIRMED"
    booking = db.get(Booking, card["booking_id"])
    db.refresh(booking)
    assert booking.status == BookingStatus.CONFIRMED and booking.confirmed_at is not None
    [mail] = emails(db)
    assert mail.title == "Booking confirmed"
    # It now behaves like any booking: no longer "held", and listed for the customer.
    mine = client.get("/api/bookings", headers=chat.headers).json()["items"]
    assert [(b["id"], b["held_in_chat"]) for b in mine] == [(card["booking_id"], False)]
    assert chat.confirm(card["id"]).status_code == 409


def test_delete_releases_the_time_without_any_email_even_inside_the_cancellation_window(client, db, make, gym, chat) -> None:
    service, sam = gym
    make.settings(cancellation_window=60 * 24 * 30)  # customers could not cancel a real booking now
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]

    deleted = chat.decline(card["id"]).json()
    assert deleted["status"] == "DECLINED"
    booking = db.get(Booking, card["booking_id"])
    db.refresh(booking)
    assert booking.status == BookingStatus.CANCELLED
    assert emails(db) == []
    other = auth_headers(client, _other(db).email)
    free = client.post("/api/bookings", headers=other, json={"service_id": str(service.id), "resource_id": str(sam.id), "start": at(TUESDAY, 10)})
    assert free.status_code == 201


def test_unanswered_holds_expire_and_free_their_time(db, gym, chat) -> None:
    service, sam = gym
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]

    freeze_time(NOW + timedelta(minutes=9))
    assert holds.release_expired(db, utcnow()) == 0
    freeze_time(NOW + timedelta(minutes=11))
    assert holds.release_expired(db, utcnow()) == 1
    db.expire_all()
    assert db.get(Booking, card["booking_id"]).status == BookingStatus.CANCELLED
    assert db.get(ChatProposal, card["id"]).status == ChatProposalStatus.EXPIRED
    assert chat.confirm(card["id"]).status_code == 409
    assert emails(db) == []


def test_confirming_after_the_hold_ran_out_releases_it_instead(db, gym, chat) -> None:
    service, sam = gym
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]
    freeze_time(NOW + timedelta(minutes=11))  # before the background job got to it
    response = chat.confirm(card["id"])
    assert response.status_code == 409 and response.json()["code"] == "HOLD_EXPIRED"
    db.expire_all()
    assert db.get(Booking, card["booking_id"]).status == BookingStatus.CANCELLED


def test_with_admin_approval_a_confirmed_hold_becomes_an_ordinary_pending_booking(client, db, make, gym, chat, admin_headers) -> None:
    service, sam = gym
    make.settings(require_admin_confirmation=True)
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]

    # While it is held, only the customer can answer it.
    held = client.get(f"/api/admin/bookings/{card['booking_id']}", headers=admin_headers).json()
    assert held["held_in_chat"] is True
    refused = client.post(f"/api/admin/bookings/{card['booking_id']}/confirm", headers=admin_headers)
    assert refused.status_code == 409 and refused.json()["code"] == "AWAITING_CUSTOMER"

    assert chat.confirm(card["id"]).json()["booking_status"] == "PENDING"
    [mail] = emails(db)
    assert mail.title == "Booking received – awaiting confirmation"
    assert client.get(f"/api/admin/bookings/{card['booking_id']}", headers=admin_headers).json()["held_in_chat"] is False
    assert client.post(f"/api/admin/bookings/{card['booking_id']}/confirm", headers=admin_headers).status_code == 200


def test_a_held_booking_cannot_be_moved_and_cancelling_it_elsewhere_deletes_it_quietly(client, db, gym, chat) -> None:
    service, sam = gym
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]
    mine = client.get(f"/api/bookings/{card['booking_id']}", headers=chat.headers).json()
    assert mine["held_in_chat"] is True and mine["can_cancel"] is True and mine["can_reschedule"] is False
    moved = client.post(f"/api/bookings/{card['booking_id']}/reschedule", headers=chat.headers, json={"start": at(TUESDAY, 14)})
    assert moved.status_code == 409 and moved.json()["code"] == "AWAITING_CUSTOMER"

    assert client.post(f"/api/bookings/{card['booking_id']}/cancel", headers=chat.headers, json={}).status_code == 200
    db.expire_all()
    assert db.get(ChatProposal, card["id"]).status == ChatProposalStatus.DECLINED
    assert emails(db) == []


def test_at_most_two_holds_are_open_at_once(db, gym, chat) -> None:
    service, sam = gym
    for hour in (10, 13):
        reply_of(chat.send(f"Book coaching with Sam Tuesday {hour}", *hold_steps(service, sam, hour=hour, duration=60)))
    third = [
        book("15:00", None),
        lambda messages: say(last_tool_results(messages)[0]["error"]),
    ]
    assert reply_of(chat.send("And Tuesday 3pm", *third))["text"] == "HOLD_LIMIT"
    assert len(db.scalars(select(Booking)).all()) == 2


@pytest.mark.parametrize(
    "time_, duration, code, other_times",
    [("20:00", 60, "OUTSIDE_AVAILABILITY", True), ("10:00", 45, "INVALID_DURATION", False), ("10:00", 240, "INVALID_DURATION", False)],
)
def test_booking_rules_come_back_to_the_model_and_nothing_is_held(db, gym, chat, time_, duration, code, other_times) -> None:
    steps = [book(time_, duration), lambda messages: say(last_tool_results(messages)[0]["error"])]
    reply = reply_of(chat.send("Book it", *steps))
    assert reply["text"] == code and reply["cards"] == []
    assert db.scalars(select(Booking)).all() == []
    # A time that is not free comes with the nearest free ones, as buttons.
    assert bool(reply["choices"]) is other_times
    if other_times:
        [result] = last_tool_results(chat.model.requests[-1]["messages"])
        assert reply["choices"] == [t["label"] for t in result["nearest_free"]]
        assert reply["choices"][-1] == "Tue 02 Mar, 4:00 PM"  # Sam works until 5 PM; the next day follows


def test_bad_tool_input_is_reported_to_the_model_as_an_error(db, gym, chat) -> None:
    steps = [
        call("book", {"service": "coaching", "date": "tomorrow", "time": "10"}),
        lambda messages: say(last_tool_results(messages)[0]["error"]),
    ]
    assert reply_of(chat.send("Book it", *steps))["text"] == "INVALID_INPUT"
    tool_result = chat.model.requests[-1]["messages"][-1]
    assert tool_result["role"] == "tool" and json.loads(tool_result["content"])["error"] == "INVALID_INPUT"


def test_tool_arguments_that_are_not_json_come_back_as_an_error(gym, chat) -> None:
    steps = [
        call_raw(("book", '{"service": "abc", "date": ')),
        lambda messages: say(last_tool_results(messages)[0]["error"]),
    ]
    assert reply_of(chat.send("Book it", *steps))["text"] == "INVALID_JSON"


def test_offered_choices_come_back_as_buttons(gym, chat) -> None:
    steps = [
        call("offer_choices", {"question": "Which coach?", "options": ["Sam Lee", "Sam Patel"]}),
        say("Which coach would you like?"),
    ]
    reply = reply_of(chat.send("Book with Sam", *steps))
    assert reply["choices"] == ["Sam Lee", "Sam Patel"]


def test_free_times_are_listed_in_the_resources_time_zone(gym, chat) -> None:
    service, sam = gym
    steps = [
        call("check_availability", {"service": "strength coaching", "date": TUESDAY.isoformat(), "days": 2}),
        lambda messages: say(str(last_tool_results(messages)[0]["days"][0]["times"][0]["label"])),
    ]
    reply = reply_of(chat.send("What's free Tuesday?", *steps))
    assert reply["text"] == "9:00 AM"
    [slots] = last_tool_results(chat.model.requests[-1]["messages"])
    assert slots["timezone"] == "Asia/Kolkata" and slots["days"][0]["label"] == "Tue 02 Mar"
    assert slots["days"][0]["times"][0]["start"] == "2027-03-02T09:00"
    assert len(slots["days"]) == 2


# ---------------------------------------------------------------- changing an existing booking


def _booked(client, headers, service, resource, start):
    response = client.post("/api/bookings", headers=headers, json={"service_id": str(service.id), "resource_id": str(resource.id), "start": start})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_move_changes_nothing_until_confirmed(client, db, gym, chat) -> None:
    service, sam = gym
    booking = _booked(client, chat.headers, service, sam, at(TUESDAY, 10))
    steps = [
        call("move", {"on_date": TUESDAY.isoformat(), "query": "session", "new_time": "14:00"}),
        say("Press Confirm to move it to 2:00 PM."),
    ]
    [card] = reply_of(chat.send("Move my Tuesday session to 2pm", *steps))["cards"]
    assert card["kind"] == "RESCHEDULE"
    assert card["was"]["time"] == "10:00 AM – 11:00 AM" and card["when"]["time"] == "2:00 PM – 3:00 PM"
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == BookingStatus.CONFIRMED

    moved = chat.confirm(card["id"]).json()
    assert moved["status"] == "CONFIRMED"
    assert moved["when"]["time"] == "2:00 PM – 3:00 PM" and moved["was"]["time"] == "10:00 AM – 11:00 AM"
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == BookingStatus.RESCHEDULED
    assert db.get(Booking, moved["booking_id"]).start_datetime == datetime(2027, 3, 2, 8, 30, tzinfo=UTC)


def test_a_move_to_a_time_that_is_not_free_is_refused_to_the_model(client, gym, chat) -> None:
    service, sam = gym
    booking = _booked(client, chat.headers, service, sam, at(TUESDAY, 10))
    steps = [
        call("move", {"booking_id": booking["id"], "new_time": "19:00"}),
        lambda messages: say(last_tool_results(messages)[0]["error"]),
    ]
    reply = reply_of(chat.send("Move it to 7pm", *steps))
    assert reply["text"] == "NOT_AVAILABLE" and reply["cards"] == []
    assert "Tue 02 Mar, 4:00 PM" in reply["choices"]  # the nearest free times, as buttons


def test_a_move_whose_new_time_was_taken_meanwhile_fails_cleanly(client, db, gym, chat) -> None:
    service, sam = gym
    booking = _booked(client, chat.headers, service, sam, at(TUESDAY, 10))
    steps = [call("move", {"booking_id": booking["id"], "new_time": "14:00"}), say("Press Confirm.")]
    [card] = reply_of(chat.send("Move it to 2pm", *steps))["cards"]
    _booked(client, auth_headers(client, _other(db).email), service, sam, at(TUESDAY, 14))

    failed = chat.confirm(card["id"]).json()
    assert failed["status"] == "FAILED" and failed["result_message"]
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == BookingStatus.CONFIRMED


def test_a_cancellation_card_and_decline(client, db, gym, chat) -> None:
    service, sam = gym
    booking = _booked(client, chat.headers, service, sam, at(TUESDAY, 10))
    steps = [call("cancel", {"booking_id": booking["id"]}), say("Press Confirm to cancel it.")]
    [card] = reply_of(chat.send("Cancel my Tuesday session", *steps))["cards"]
    assert card["kind"] == "CANCEL" and card["when"]["time"] == "10:00 AM – 11:00 AM"

    assert chat.decline(card["id"]).json()["status"] == "DECLINED"
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == BookingStatus.CONFIRMED
    assert chat.confirm(card["id"]).status_code == 409

    steps = [call("cancel", {"booking_id": booking["id"]}), say("Press Confirm to cancel it.")]
    [card] = reply_of(chat.send("Actually, cancel it", *steps))["cards"]
    assert chat.confirm(card["id"]).json()["status"] == "CONFIRMED"
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == BookingStatus.CANCELLED


def test_customers_only_ever_reach_their_own_bookings_and_cards(client, db, gym, chat) -> None:
    service, sam = gym
    other = _other(db)
    other_headers = auth_headers(client, other.email)
    theirs = _booked(client, other_headers, service, sam, at(TUESDAY, 15))
    steps = [
        call("cancel", {"booking_id": theirs["id"]}, ("move", {"booking_id": theirs["id"], "new_time": "16:00"})),
        lambda messages: say(" ".join(r["error"] for r in last_tool_results(messages))),
    ]
    reply = reply_of(chat.send("Show booking", *steps))
    assert reply["text"] == "NOT_FOUND NOT_FOUND"

    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]
    client.post("/api/chat/subscription", headers=other_headers)
    assert client.post(f"/api/chat/proposals/{card['id']}/confirm", headers=other_headers).status_code == 404
    assert client.get(f"/api/chat/conversations/{chat.conversation}", headers=other_headers).status_code == 404


# ---------------------------------------------------------------- history and what is shown


def test_history_is_append_only_and_the_customer_sees_only_the_conversation(db, gym, chat) -> None:
    service, sam = gym
    reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))
    reply_of(chat.send("Thanks!", say("You're welcome!")))

    last_of_first_turn = chat.model.requests[1]["messages"]
    second_turn = chat.model.requests[2]["messages"]
    stored_reply = db.scalars(select(ChatMessage).where(ChatMessage.role == ChatRole.ASSISTANT).order_by(ChatMessage.seq)).all()[1]
    # Everything the model saw before is sent again unchanged, the last reply after it.
    assert second_turn[: len(last_of_first_turn)] == last_of_first_turn
    assert second_turn[len(last_of_first_turn)] == stored_reply.content
    assert stored_reply.content == {"role": "assistant", "content": HELD_REPLY}
    assert second_turn[-2:] == [{"role": "user", "content": "Thanks!"}, second_turn[-1]] and second_turn[-1]["role"] == "system"

    items = chat.transcript()
    assert [(i["role"], i["text"][:12]) for i in items] == [
        ("user", "Book coachin"),
        ("assistant", "I've held St"),
        ("user", "Thanks!"),
        ("assistant", "You're welco"),
    ]
    assert items[1]["cards"][0]["kind"] == "HOLD"
    assert "tool" not in str(items).lower() and "Current time" not in str(items)


def test_how_a_card_ended_is_told_to_the_model_next_turn(gym, chat) -> None:
    service, sam = gym
    [card] = reply_of(chat.send("Book coaching with Sam Tuesday 10 for 2 hours", *hold_steps(service, sam)))["cards"]
    chat.decline(card["id"])
    reply_of(chat.send("Hmm", say("Shall I find another time?")))
    note = chat.model.requests[-1]["messages"][-1]
    assert note["role"] == "system" and "deleted the held booking Strength coaching" in note["content"]
    reply_of(chat.send("Yes", say("Sure.")))
    assert "Since your last reply" not in chat.model.requests[-1]["messages"][-1]["content"]


def test_the_faq_written_by_the_business_is_part_of_the_prompt(client, admin_headers, gym, chat) -> None:
    saved = client.put("/api/admin/settings", headers=admin_headers, json={"values": {"assistant_knowledge": "Free parking behind the gym."}})
    assert saved.status_code == 200, saved.text
    reply_of(chat.send("Is there parking?", say("Yes, free parking behind the gym.")))
    system = chat.model.requests[-1]["system"]
    assert "<business_faq>\nFree parking behind the gym.\n</business_faq>" in system
    assert client.put("/api/admin/settings", headers=admin_headers, json={"values": {"assistant_knowledge": ""}}).status_code == 200


# ---------------------------------------------------------------- limits and feedback


def test_message_limits(client, db, gym, chat, settings) -> None:
    assert chat.send("x" * 1001).status_code == 422
    settings(chat_daily_message_limit=1)
    reply_of(chat.send("Hi", say("Hello!")))
    limited = chat.send("Hi again")
    assert limited.status_code == 429 and limited.json()["code"] == "CHAT_LIMIT_REACHED"


def test_one_reply_at_a_time_per_conversation(db, gym, chat) -> None:
    conversation = db.get(ChatConversation, chat.conversation)
    conversation.busy_until = utcnow() + timedelta(minutes=1)
    db.commit()
    busy = chat.send("Hi")
    assert busy.status_code == 409 and busy.json()["code"] == "CHAT_BUSY"


def test_a_turn_that_keeps_calling_tools_stops_cleanly(gym, chat, settings) -> None:
    settings(chat_max_tool_rounds=3)
    reply = reply_of(chat.send("Book something", *[call("check_availability", {"date": TUESDAY.isoformat()}) for _ in range(3)]))
    assert "couldn't finish" in reply["text"]
    reply_of(chat.send("Hi", say("Hello!")))
    # The unfinished turn is never sent to the model again.
    assert [m["content"] for m in chat.model.requests[-1]["messages"] if m["role"] == "user"] == ["Hi"]


def test_feedback_on_replies(client, db, gym, chat) -> None:
    reply = reply_of(chat.send("Hi", say("Hello!")))
    ok = client.post(f"/api/chat/messages/{reply['id']}/feedback", headers=chat.headers, json={"value": -1})
    assert ok.json()["feedback"] == -1
    [mine] = [i for i in chat.transcript() if i["role"] == "user"]
    assert client.post(f"/api/chat/messages/{mine['id']}/feedback", headers=chat.headers, json={"value": 1}).status_code == 404
    other_headers = auth_headers(client, _other(db).email)
    client.post("/api/chat/subscription", headers=other_headers)
    assert client.post(f"/api/chat/messages/{reply['id']}/feedback", headers=other_headers, json={"value": 1}).status_code == 404


# ---------------------------------------------------------------- Groq limits and errors


def _raise(exc):
    def step(messages):
        raise exc

    return step


def test_a_rate_limited_turn_says_busy_releases_its_hold_and_is_not_a_strike(db, gym, chat) -> None:
    from app.services.chat.groq import RateLimited

    service, sam = gym
    steps = [
        book("10:00", None),
        _raise(RateLimited(30)),
    ]
    reply = reply_of(chat.send("Book Sam Tuesday 10", *steps))
    assert reply["text"].startswith("The assistant is busy right now")
    db.expire_all()
    assert db.scalars(select(Booking)).one().status == BookingStatus.CANCELLED  # the hold is released
    assert db.scalar(select(ChatMessage.blocked_reason).where(ChatMessage.blocked_reason.is_not(None))) == "rate limited"

    reply = reply_of(chat.send("Hi", _raise(RateLimited(3600))))
    assert reply["text"].startswith("The assistant has reached today's limit")


def test_a_tool_call_the_server_could_not_read_is_asked_for_again_with_a_hint(gym, chat, caplog) -> None:
    from app.services.chat.agent import TOOL_CALL_HINT
    from app.services.chat.groq import ToolCallFailed

    bad = ToolCallFailed("Failed to call a function", "list_services<|channel|>{oops")
    assert reply_of(chat.send("Hi", _raise(bad), _raise(bad), say("Hello!")))["text"] == "Hello!"
    first, second, third = chat.model.requests
    assert TOOL_CALL_HINT not in str(first["messages"])
    for request in (second, third):
        hint = request["messages"][-1]
        assert hint["role"] == "system" and hint["content"].startswith(TOOL_CALL_HINT)
        assert "Failed to call a function" in hint["content"]  # what was wrong goes back to the model
    assert "list_services<|channel|>{oops" in caplog.text  # what the model wrote is logged
    assert TOOL_CALL_HINT not in str(chat.transcript())  # the hint is never stored

    reply = reply_of(chat.send("Hi", _raise(bad), _raise(bad), _raise(bad)))
    assert "couldn't finish" in reply["text"]


def test_an_unfinished_turn_logs_why(gym, chat, caplog) -> None:
    looping = [call("cancel", {"booking_id": "not-a-uuid"}) for _ in range(8)]
    reply = reply_of(chat.send("Hi", *looping))
    assert "couldn't finish" in reply["text"]
    assert "Chat turn unfinished" in caplog.text and "cancel" in caplog.text and "INVALID_INPUT" in caplog.text


def test_services_on_a_date_say_which_have_free_times(gym, chat) -> None:
    service, _ = gym
    reply_of(chat.send("What can I book on Tuesday?", call("check_availability", {"date": TUESDAY.isoformat()}), say("Strength coaching has times.")))
    [result] = last_tool_results(chat.model.requests[-1]["messages"])
    [entry] = result["services"]
    assert entry["name"] == service.name
    free = entry["free_times_on_date"]
    assert free["date"] == TUESDAY.isoformat() and free["count"] > 0 and free["first"][0] == "9:00 AM"


def test_only_the_last_turns_are_sent_and_never_cut_inside_a_tool_call(gym, chat, settings) -> None:
    settings(chat_history_turns=2)
    reply_of(chat.send("first", say("A")))
    reply_of(chat.send("second", call("check_availability", {"date": TUESDAY.isoformat()}), say("B")))
    reply_of(chat.send("third", say("C")))
    sent = chat.model.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["user", "system", "assistant", "tool", "assistant", "user", "system"]
    assert sent[0] == {"role": "user", "content": "second"}
    assert sent[2]["tool_calls"][0]["id"] == sent[3]["tool_call_id"]


def test_older_turns_are_replayed_as_text_and_the_previous_turn_in_full(gym, chat) -> None:
    reply_of(chat.send("first", call("check_availability", {"date": TUESDAY.isoformat()}), say("A")))
    reply_of(chat.send("second", call("check_availability", {"date": TUESDAY.isoformat()}), say("B")))
    reply_of(chat.send("third", call("check_availability", {"date": TUESDAY.isoformat()}), say("C")))
    reply_of(chat.send("fourth", say("D")))
    sent = chat.model.requests[-1]["messages"]
    assert [m["role"] for m in sent] == [
        "user", "assistant",  # first and second: only what was said
        "user", "assistant",
        "user", "system", "assistant", "tool", "assistant",  # the previous turn: with its tool call and result
        "user", "system",  # the new message
    ]
    assert sent[1] == {"role": "assistant", "content": "A"}


def test_placeholders_for_unknown_ids_are_left_out_instead_of_failing(client, db, make, gym, user, user_headers) -> None:
    import uuid as _uuid

    from app.services.chat import tools

    service, sam = gym
    ctx = tools.ToolContext(user_id=user.id, conversation_id=_uuid.uuid4(), turn_id=_uuid.uuid4())
    # An unknown optional value ("?") is simply not given...
    content, failed = tools.run("check_availability", {"date": TUESDAY.isoformat(), "service": "strength", "person": "?"}, ctx)
    assert not failed and json.loads(content)["service"] == "Strength coaching"
    # ...and for a required one the model is told it is missing.
    content, failed = tools.run("book", {"service": "?", "date": TUESDAY.isoformat(), "time": "10:00"}, ctx)
    assert failed and "service" in json.loads(content)["message"]
    # Groq checks the schema before the server does, so it must not insist on a uuid format.
    assert all("format" not in p for t in tools.TOOLS for p in t.schema.get("properties", {}).values())


def test_a_move_to_another_length_holds_a_new_session_and_offers_to_cancel_the_old_one(client, db, gym, chat) -> None:
    service, sam = gym
    booking = _booked(client, chat.headers, service, sam, at(TUESDAY, 10))  # one hour
    steps = [call("move", {"booking_id": booking["id"], "new_time": "14:00", "duration_minutes": 120}), say("Here are the two cards.")]
    reply = reply_of(chat.send("Move it to 2pm for two hours", *steps))
    hold, cancel = reply["cards"]
    assert hold["kind"] == "HOLD" and hold["when"]["time"] == "2:00 PM – 4:00 PM"
    assert cancel["kind"] == "CANCEL" and cancel["booking_id"] == booking["id"]
    [result] = last_tool_results(chat.model.requests[-1]["messages"])
    assert "keeps a booking's length (60 minutes)" in result["next_step"]

    # Without a new length it is an ordinary move, which keeps the hour.
    other = _booked(client, chat.headers, service, sam, at(TUESDAY + timedelta(days=1), 10))
    reply = reply_of(chat.send("Move the Wednesday one to 2pm", call("move", {"booking_id": other["id"], "new_time": "14:00"}), say("Press Confirm.")))
    [card] = reply["cards"]
    assert card["kind"] == "RESCHEDULE" and card["when"]["time"] == "2:00 PM – 3:00 PM"


def test_a_held_booking_is_changed_by_replacing_it(db, make, gym, client, chat) -> None:
    service, sam = gym
    pat = make.resource("Coach Pat", services=[service], location=sam.location, hours=make.weekdays(time(9), time(17)))
    reply_of(chat.send("Book Sam Tuesday 10 for two hours", book(), say("Held 10:00 AM – 12:00 PM.")))
    [held] = last_tool_results(chat.model.requests[-1]["messages"])

    # A time that is not free leaves the held booking alone, and offers the nearest free ones.
    _booked(client, chat.headers, service, pat, at(TUESDAY, 14))
    reply = reply_of(chat.send("Make it 2pm with Pat", call("move", {"booking_id": held["booking_id"], "person": "Pat", "new_time": "14:00"}), say("Sorry, that is taken.")))
    [failed] = last_tool_results(chat.model.requests[-1]["messages"])
    assert failed["error"] == "NOT_AVAILABLE" and reply["choices"]
    db.expire_all()
    assert db.get(Booking, uuid.UUID(held["booking_id"])).status == BookingStatus.PENDING

    # "Change that to Pat": the hold is swapped, keeping its time and length.
    reply = reply_of(chat.send("Change that to Pat", call("move", {"booking_id": held["booking_id"], "person": "Pat"}), say("Held with Pat instead.")))
    db.expire_all()
    assert db.get(Booking, uuid.UUID(held["booking_id"])).status == BookingStatus.CANCELLED
    [card] = reply["cards"]
    assert card["kind"] == "HOLD" and card["resource"] == "Coach Pat" and card["when"]["time"] == "10:00 AM – 12:00 PM"


def test_book_resolves_names_and_asks_when_it_is_not_sure(db, make, gym, chat) -> None:
    service, sam = gym
    training = make.service("Personal Training", duration=60, price=Decimal("30"))
    make.resource("Coach Pat", services=[training], location=sam.location, hours=make.weekdays(time(9), time(17)))
    make.resource("Sam Lee", services=[service], location=sam.location, hours=make.weekdays(time(9), time(17)))

    def result_of(text, args):
        reply = reply_of(chat.send(text, call("book", {"date": TUESDAY.isoformat(), "time": "10:00", **args}), say("Which one?")))
        return reply, last_tool_results(chat.model.requests[-1]["messages"])[0]

    # A name that is not in the catalogue is never guessed: the closest names are offered.
    reply, result = result_of("Book personal coaching", {"service": "personal coaching"})
    assert result["needs"] == "service" and "Personal Training" in reply["choices"]
    assert "does not exist" in result["next_step"]  # told never to deny that an option exists
    # Two people with that name: both are offered.
    reply, result = result_of("Strength coaching with Sam", {"service": "strength coaching", "person": "Sam"})
    assert result["needs"] == "person" and sorted(reply["choices"]) == ["Coach Sam", "Sam Lee"]
    # Someone who doesn't offer the service: the people who do are offered.
    reply, result = result_of("Strength coaching with Pat", {"service": "strength coaching", "person": "Pat"})
    assert result["needs"] == "person" and "Coach Pat does not offer Strength coaching" in result["next_step"]
    assert sorted(reply["choices"]) == ["Coach Sam", "Sam Lee"]
    assert db.scalars(select(Booking)).all() == []

    # Typos in a name that is clearly meant are fine.
    reply, result = result_of("Book strenght coaching with Lee", {"service": "strenght coaching", "person": "Lee"})
    assert result["held"] is True and result["booking"]["resource"] == "Sam Lee"


def test_a_change_asks_which_booking_when_several_match(client, gym, chat) -> None:
    service, sam = gym
    _booked(client, chat.headers, service, sam, at(TUESDAY, 10))
    _booked(client, chat.headers, service, sam, at(TUESDAY, 14))
    reply = reply_of(chat.send("Cancel my Tuesday session", call("cancel", {"on_date": TUESDAY.isoformat()}), say("Which one?")))
    [result] = last_tool_results(chat.model.requests[-1]["messages"])
    assert len(result["which"]) == 2 and reply["cards"] == []
    assert reply["choices"] == ["Strength coaching, Tue 02 Mar 2027, 10:00 AM", "Strength coaching, Tue 02 Mar 2027, 2:00 PM"]

    reply = reply_of(chat.send("The 2pm one", call("cancel", {"on_date": TUESDAY.isoformat(), "around_time": "14:00"}), say("Press Confirm to cancel it.")))
    [card] = reply["cards"]
    assert card["kind"] == "CANCEL" and card["when"]["time"] == "2:00 PM – 3:00 PM"


def test_a_reply_that_contradicts_its_card_is_replaced_by_the_card_in_words(client, gym, chat) -> None:
    service, sam = gym
    booking = _booked(client, chat.headers, service, sam, at(TUESDAY, 10))
    steps = [call("move", {"booking_id": booking["id"], "new_time": "14:00"}), say("I'll move it to **2:00 PM – 4:00 PM**. Press Confirm.")]
    reply = reply_of(chat.send("Move it to 2pm", *steps))
    assert "4:00 PM" not in reply["text"] and "**" not in reply["text"]
    assert "2:00 PM" in reply["text"] and "3:00 PM" in reply["text"] and "Coach Sam" in reply["text"]
    assert len(reply["cards"]) == 1

    # A reply that agrees with the card is kept, minus any Markdown.
    other = _booked(client, chat.headers, service, sam, at(TUESDAY + timedelta(days=1), 10))
    steps = [call("move", {"booking_id": other["id"], "new_time": "14:00"}), say("Moving it to **2:00 PM**. Press Confirm.")]
    assert reply_of(chat.send("And this one to 2pm", *steps))["text"] == "Moving it to 2:00 PM. Press Confirm."


# ---------------------------------------------------------------- finding the booking the customer means


def _find(user, **filters) -> dict:
    import uuid as _uuid

    from app.services.chat import tools

    ctx = tools.ToolContext(user_id=user.id, conversation_id=_uuid.uuid4(), turn_id=_uuid.uuid4())
    content, _ = tools.run("my_bookings", filters, ctx)
    return json.loads(content)


def test_list_my_bookings_narrows_down_to_the_booking_meant(client, db, make, gym, user, user_headers) -> None:
    service, sam = gym
    yoga = make.service("Yoga Class", duration=60, price=Decimal("12"))
    studio = make.resource("Yoga Studio", services=[yoga], location=sam.location, hours=make.weekdays(time(6), time(22)))
    _booked(client, user_headers, service, sam, at(TUESDAY, 10))  # 10:00 in Kolkata
    _booked(client, user_headers, yoga, studio, at(TUESDAY, 18))
    _booked(client, user_headers, yoga, studio, at(TUESDAY, 20))
    _booked(client, user_headers, yoga, studio, at(TUESDAY + timedelta(days=2), 18))
    _booked(client, auth_headers(client, _other(db).email), yoga, studio, at(TUESDAY, 21))  # someone else's

    def names(result):
        return [(b["service"], b["time"].split(" – ")[0]) for b in result["bookings"]]

    assert len(_find(user)["bookings"]) == 4
    tuesday = TUESDAY.isoformat()
    assert names(_find(user, on_date=tuesday, query="yoga")) == [("Yoga Class", "6:00 PM"), ("Yoga Class", "8:00 PM")]
    assert _find(user, on_date=tuesday, query="yoga")["match_count"] == 2  # two: the assistant must ask
    assert names(_find(user, on_date=tuesday, around_time="19:45", query="yoga")) == [("Yoga Class", "8:00 PM")]
    assert names(_find(user, around_time="10:30")) == [("Strength coaching", "10:00 AM")]  # in the booking's time zone
    assert names(_find(user, query="strenght coachng")) == [("Strength coaching", "10:00 AM")]  # typos
    assert names(_find(user, query="Sam")) == [("Strength coaching", "10:00 AM")]

    none = _find(user, query="tennis")
    assert none["match_count"] == 0 and len(none["other_upcoming"]) == 4
    assert all(b["time"] != "9:00 PM – 10:00 PM" for b in _find(user, on_date=tuesday)["bookings"])
