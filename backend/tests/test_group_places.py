"""Group sessions: several people are one booking with several places, free times say how
many places are left, and a customer already in a session changes their places instead of
booking again (from a connected assistant and from the in-app chat)."""

from datetime import time
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Booking, Notification
from app.models.enums import BookingStatus, BookingType, NotificationChannel, NotificationType, ResourceType
from tests.agent_helpers import Agent, connect
from tests.chat_fakes import call, reply_of, say
from tests.conftest import MONDAY

DAY = MONDAY.isoformat()


@pytest.fixture
def yoga(make):
    """A 60-minute yoga class for up to 5 people at 12.00 a place, weekdays 09:00-17:00 UTC."""
    service = make.service("Yoga Class", duration=60, price=Decimal("12"), booking_type=BookingType.CAPACITY, capacity=5)
    studio = make.resource("Yoga Studio", services=[service], hours=make.weekdays(time(9), time(17)), type=ResourceType.ROOM, capacity=15)
    return service, studio


@pytest.fixture
def agent(client, user_headers) -> Agent:
    assert client.post("/api/chat/subscription", headers=user_headers).json()["active"]
    return Agent(client, connect(client, user_headers)["access_token"])


def book(agent: Agent, places: int = 1, hour: str = "10:00") -> dict:
    [action] = agent.call("hold_booking", service="yoga", date=DAY, time=hour, quantity=places)["actions"]
    done = agent.call("confirm_action", action_id=action["action_id"], expected_price=action["price"])
    assert done["status"] == "booked", done
    return done


def at_ten(free: dict) -> dict:
    return next(t for t in free["days"][0]["times"] if t["label"] == "10:00 AM")


def test_three_people_are_one_booking_with_three_places(db, agent, yoga) -> None:
    held = agent.call("hold_booking", service="yoga", date=DAY, time="10:00", quantity=3)
    [action] = held["actions"]
    assert action["places"] == 3 and action["price"] == "36.00"  # charged per place
    assert agent.call("confirm_action", action_id=action["action_id"], expected_price="36.00")["status"] == "booked"
    [booking] = db.scalars(select(Booking)).all()
    assert booking.quantity == 3 and booking.price == Decimal("36.00")


def test_free_times_say_how_many_places_are_left_and_which_session_is_yours(agent, yoga) -> None:
    free = agent.call("check_availability", date=DAY, service="yoga")
    assert free["group_session"] is True and free["places_per_session"] == 5
    assert at_ten(free)["places_left"] == 5 and "your_booking" not in at_ten(free)

    done = book(agent, places=2)
    free = agent.call("check_availability", date=DAY, service="yoga")
    assert at_ten(free)["places_left"] == 3
    assert at_ten(free)["your_booking"] == {"booking_id": done["booking_id"], "places": 2}
    # Asking for more people than are left hides the session.
    assert "10:00 AM" not in [t["label"] for t in agent.call("check_availability", date=DAY, service="yoga", quantity=4)["days"][0]["times"]]


def test_a_session_you_are_in_is_not_booked_again_and_no_other_dates_are_offered(agent, yoga) -> None:
    done = book(agent)
    again = agent.call("hold_booking", service="yoga", date=DAY, time="10:00", quantity=3)
    assert again["error"] == "ALREADY_BOOKED" and "already has a booking for this session" in again["message"]
    assert again["your_booking"] == {"booking_id": done["booking_id"], "places": 1}
    assert "nearest_free" not in again and "propose_reschedule" in again["next_step"]


def test_places_change_only_when_confirmed_at_the_new_price(db, agent, yoga) -> None:
    done = book(agent)
    [change] = agent.call("propose_reschedule", booking_id=done["booking_id"], places=3)["actions"]
    assert change["kind"] == "change_places" and change["places_was"] == 1 and change["places"] == 3 and change["price"] == "36.00"
    assert db.get(Booking, done["booking_id"]).quantity == 1  # nothing changed yet

    assert agent.call("confirm_action", action_id=change["action_id"], expected_price="12.00")["error"] == "PRICE_MISMATCH"
    changed = agent.call("confirm_action", action_id=change["action_id"], expected_price="36.00")
    assert changed["status"] == "changed" and changed["booking"]["quantity"] == 3
    db.expire_all()
    booking = db.get(Booking, done["booking_id"])
    assert booking.quantity == 3 and booking.price == Decimal("36.00") and booking.status == BookingStatus.CONFIRMED
    note = db.scalar(select(Notification).where(Notification.type == NotificationType.BOOKING_UPDATED, Notification.channel == NotificationChannel.EMAIL))
    assert note is not None and "Now for 3 places (was 1)" in note.body


def test_places_must_fit_the_session(agent, make, yoga) -> None:
    done = book(agent)
    too_many = agent.call("propose_reschedule", booking_id=done["booking_id"], places=6)
    assert too_many["error"] == "FULL" and "room for 6" in too_many["message"]


def test_fewer_places_cost_less(db, agent, yoga) -> None:
    done = book(agent, places=3)
    [change] = agent.call("propose_reschedule", booking_id=done["booking_id"], places=1)["actions"]
    assert change["price"] == "12.00"
    agent.call("confirm_action", action_id=change["action_id"], expected_price="12.00")
    db.expire_all()
    assert db.get(Booking, done["booking_id"]).quantity == 1


def test_one_change_at_a_time(agent, yoga) -> None:
    done = book(agent)
    both = agent.call("propose_reschedule", booking_id=done["booking_id"], places=2, new_time="11:00")
    assert both["error"] == "ONE_CHANGE_AT_A_TIME"


def test_the_spending_limit_counts_the_extra_places(client, user_headers, yoga) -> None:
    assert client.post("/api/chat/subscription", headers=user_headers).json()["active"]
    agent = Agent(client, connect(client, user_headers, weekly_spend_limit="50")["access_token"])
    done = book(agent, places=2)  # 24.00
    [change] = agent.call("propose_reschedule", booking_id=done["booking_id"], places=5)["actions"]  # 60.00, 36.00 more
    assert agent.call("confirm_action", action_id=change["action_id"], expected_price="60.00")["error"] == "SPENDING_LIMIT"


# ---------------------------------------------------------------- the in-app assistant


def test_the_chat_changes_places_with_a_card(client, db, yoga, chat) -> None:
    reply = reply_of(chat.send("Yoga on Monday at 10", call("book", {"service": "yoga", "date": DAY, "time": "10:00"}), say("Held.")))
    [held] = reply["cards"]
    chat.confirm(held["id"])

    reply = reply_of(chat.send("Make that for 3 of us", call("move", {"booking_id": held["booking_id"], "places": 3}), say("Press Confirm.")))
    [card] = reply["cards"]
    assert card["kind"] == "PLACES" and card["quantity"] == 3 and card["places_was"] == 1 and card["price"] == "36.00"
    assert chat.confirm(card["id"]).json()["status"] == "CONFIRMED"
    db.expire_all()
    assert db.get(Booking, held["booking_id"]).quantity == 3


def test_a_held_booking_is_held_again_for_more_people(client, db, yoga, chat) -> None:
    [held] = reply_of(chat.send("Yoga on Monday at 10", call("book", {"service": "yoga", "date": DAY, "time": "10:00"}), say("Held.")))["cards"]
    [again] = reply_of(chat.send("Actually 2 of us", call("move", {"booking_id": held["booking_id"], "places": 2}), say("Held.")))["cards"]
    assert again["kind"] == "HOLD" and again["quantity"] == 2 and again["price"] == "24.00"
    db.expire_all()
    assert db.get(Booking, held["booking_id"]).status == BookingStatus.CANCELLED
