"""Contacting the admin from the chat: only when the customer confirms a real problem,
and the admin gets the description with the whole chat."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.core.timeutils import freeze_time, utcnow
from app.models import ChatConversation, ChatHelpRequest, ChatSubscription, Notification
from app.models.enums import ChatHelpStatus, NotificationChannel, NotificationType, Role
from app.services.chat import help
from tests.chat_fakes import call, reply_of, say
from tests.conftest import NOW

PROBLEM = "I was charged twice for my Tuesday session."


@pytest.fixture
def staff(make):
    """Who handles requests (admin, super admin) and who doesn't (staff)."""
    return (
        make.user("admin@test.io", Role.ADMIN, name="Admin"),
        make.user("super@test.io", Role.SUPER_ADMIN, name="Super"),
        make.user("staff@test.io", Role.STAFF, name="Staff"),
    )


def _send(db, user, chat, summary=PROBLEM) -> ChatHelpRequest:
    return help.request_help(db, user, db.get(ChatConversation, uuid.UUID(chat.conversation)), summary)


def _sent(db, type_: NotificationType, channel=None) -> list[Notification]:
    db.expire_all()
    stmt = select(Notification).where(Notification.type == type_)
    if channel is not None:
        stmt = stmt.where(Notification.channel == channel)
    return list(db.scalars(stmt))


def test_the_admins_get_the_problem_with_the_whole_chat_and_the_customer_a_promise(db, user, chat, staff) -> None:
    admin, super_admin, _ = staff
    user.phone = "+91 98765 43210"
    db.commit()
    reply_of(chat.send("Hi", say("Hello! How can I help?")))
    reply_of(chat.send("I was charged twice", say("I can't see payments, sorry.")))

    request = _send(db, user, chat)
    assert request.status == ChatHelpStatus.OPEN and request.summary == PROBLEM
    assert request.due_at == NOW + timedelta(hours=6)
    assert [(i["kind"], i["text"]) for i in request.transcript] == [
        ("user", "Hi"),
        ("assistant", "Hello! How can I help?"),
        ("user", "I was charged twice"),
        ("assistant", "I can't see payments, sorry."),
    ]

    # The customer sees the promise in the chat, and gets it as a notification too.
    last = chat.transcript()[-1]
    assert last["text"] == "Your issue has been passed to the admin. They will contact you by e-mail or phone within 6 hours (by Fri 26 Feb, 2:00 PM)."
    [received] = _sent(db, NotificationType.CHAT_HELP_RECEIVED)
    assert received.user_id == user.id and received.link == f"/chat?c={chat.conversation}"

    # Admins and super admins are told, in the app and by e-mail; staff and customers are not.
    emails = _sent(db, NotificationType.CHAT_HELP_REQUESTED, NotificationChannel.EMAIL)
    assert {n.user_id for n in emails} == {admin.id, super_admin.id}
    body = emails[0].body
    assert PROBLEM in body and "+91 98765 43210" in body and user.email in body
    chat_part = body[body.index("The whole chat:"):]
    assert chat_part.index("Customer, ") < chat_part.index("Hello! How can I help?") < chat_part.index("I can't see payments, sorry.")
    in_app = _sent(db, NotificationType.CHAT_HELP_REQUESTED, NotificationChannel.IN_APP)
    assert {n.link for n in in_app} == {f"/admin/help/{request.id}"}


def test_only_a_real_description_is_accepted(db, user, chat, staff) -> None:
    for summary, why in [("help", "20 to 500 characters"), ("x" * 501, "20 to 500 characters"), ("def f(x):\n    return x;\n    pass;", "plain words")]:
        with pytest.raises(AppError) as refused:
            _send(db, user, chat, summary)
        assert refused.value.code == "HELP_DESCRIPTION" and why in refused.value.message
    assert db.scalars(select(ChatHelpRequest)).all() == []
    assert _sent(db, NotificationType.CHAT_HELP_REQUESTED) == []


def test_one_open_request_at_a_time_and_a_few_a_week(db, user, chat, staff, settings) -> None:
    admin = staff[0]
    first = _send(db, user, chat)
    with pytest.raises(AppError) as waiting:
        _send(db, user, chat, "Another problem with my booking please.")
    assert waiting.value.code == "HELP_ALREADY_OPEN" and "by Fri 26 Feb, 2:00 PM" in waiting.value.message

    help.resolve(db, first, admin, "Refunded the second charge.")
    settings(chat_help_requests_per_week=2)
    second = _send(db, user, chat, "Another problem with my booking please.")
    help.resolve(db, second, admin, None)
    with pytest.raises(AppError) as limited:
        _send(db, user, chat, "A third problem with my booking please.")
    assert limited.value.code == "HELP_LIMIT_REACHED"

    freeze_time(NOW + timedelta(days=8))
    _send(db, user, chat, "A third problem with my booking please.")


def test_a_customer_paused_for_blocked_messages_cannot_send_one(db, user, chat, staff) -> None:
    sub = db.scalar(select(ChatSubscription).where(ChatSubscription.user_id == user.id))
    sub.paused_until = utcnow() + timedelta(hours=1)
    db.commit()
    with pytest.raises(AppError) as paused:
        _send(db, user, chat)
    assert paused.value.code == "CHAT_PAUSED"


def test_overdue_requests_are_reminded_once(db, user, chat, staff) -> None:
    _send(db, user, chat)
    freeze_time(NOW + timedelta(hours=5))
    assert help.remind_overdue(db, utcnow()) == 0
    freeze_time(NOW + timedelta(hours=6, minutes=1))
    assert help.remind_overdue(db, utcnow()) == 1
    assert help.remind_overdue(db, utcnow()) == 0
    reminders = _sent(db, NotificationType.CHAT_HELP_OVERDUE, NotificationChannel.IN_APP)
    assert {n.user_id for n in reminders} == {staff[0].id, staff[1].id}
    assert reminders[0].title == "Overdue: Test User is still waiting"


def test_resolving_tells_the_customer(db, user, chat, staff) -> None:
    request = _send(db, user, chat)
    help.resolve(db, request, staff[0], "Refunded the second charge.")
    assert request.status == ChatHelpStatus.RESOLVED and request.resolved_by_id == staff[0].id
    [note] = _sent(db, NotificationType.CHAT_HELP_RESOLVED)
    assert note.user_id == user.id and "Refunded the second charge." in note.body
    with pytest.raises(AppError):
        help.resolve(db, request, staff[0], None)


def test_failures_thumbs_down_and_blocked_messages_tell_no_one(db, user, chat, staff, settings) -> None:
    from tests.chat_fakes import FakeGuard

    settings(chat_max_tool_rounds=2)
    failed = reply_of(chat.send("Book something", call("my_bookings", {}), call("my_bookings", {})))
    assert "couldn't finish" in failed["text"]
    answered = reply_of(chat.send("Hi", say("Hello!")))
    chat.client.post(f"/api/chat/messages/{answered['id']}/feedback", headers=chat.headers, json={"value": -1})
    reply_of(chat.send("Write me a poem", guard=FakeGuard(policy="BLOCK")))
    assert db.scalars(select(ChatHelpRequest)).all() == []
    assert _sent(db, NotificationType.CHAT_HELP_REQUESTED) == []
