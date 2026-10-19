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


# ---------------------------------------------------------------- the two ways in


def test_the_assistant_offers_a_card_and_nothing_is_sent_until_the_customer_presses_send(db, user, chat, staff) -> None:
    steps = [call("contact_admin", {"summary": PROBLEM}), say("I've written it down. Press Send to admin and they'll contact you within 6 hours.")]
    reply = reply_of(chat.send("I was charged twice, I want to talk to someone", *steps))
    [card] = reply["cards"]
    assert card["kind"] == "HANDOFF" and card["summary"] == PROBLEM and card["response_hours"] == 6
    assert db.scalars(select(ChatHelpRequest)).all() == []
    assert _sent(db, NotificationType.CHAT_HELP_REQUESTED) == []

    sent = chat.confirm(card["id"]).json()
    assert sent["status"] == "CONFIRMED" and sent["result_message"].startswith("Your issue has been passed to the admin")
    [request] = db.scalars(select(ChatHelpRequest)).all()
    assert request.summary == PROBLEM
    assert [i["text"] for i in request.transcript][:1] == ["I was charged twice, I want to talk to someone"]
    assert len(_sent(db, NotificationType.CHAT_HELP_REQUESTED, NotificationChannel.EMAIL)) == 2
    assert chat.transcript()[-1]["text"].startswith("Your issue has been passed to the admin")

    # The assistant is told next turn.
    reply_of(chat.send("Thanks", say("You're welcome.")))
    assert "sent their problem to the admin" in chat.model.requests[-1]["messages"][-1]["content"]


def test_no_thanks_sends_nothing(db, user, chat, staff) -> None:
    [card] = reply_of(chat.send("I want a person", call("contact_admin", {"summary": PROBLEM}), say("Press Send to admin.")))["cards"]
    assert chat.decline(card["id"]).json()["status"] == "DECLINED"
    assert db.scalars(select(ChatHelpRequest)).all() == []
    assert _sent(db, NotificationType.CHAT_HELP_REQUESTED) == []


def test_the_assistant_is_told_when_a_request_is_already_open(db, user, chat, staff) -> None:
    from tests.chat_fakes import last_tool_results

    _send(db, user, chat)
    reply = reply_of(chat.send("Another problem", call("contact_admin", {"summary": "Another problem with my booking please."}), say("It's already with the admin.")))
    assert reply["cards"] == []
    [result] = last_tool_results(chat.model.requests[-1]["messages"])
    assert result["error"] == "HELP_ALREADY_OPEN"


def test_after_the_assistant_failed_the_customer_can_send_their_own_description(client, db, user, chat, staff, settings) -> None:
    settings(chat_max_tool_rounds=1)
    failed = reply_of(chat.send("Book something", call("my_bookings", {})))
    assert "couldn't finish" in failed["text"] and failed["contact_admin"] is True
    assert reply_of(chat.send("Hi", say("Hello!")))["contact_admin"] is False

    url = f"/api/chat/conversations/{chat.conversation}/help"
    short = client.post(url, headers=chat.headers, json={"summary": "help"})
    assert short.status_code == 422 and short.json()["code"] == "HELP_DESCRIPTION"
    sent = client.post(url, headers=chat.headers, json={"summary": PROBLEM})
    assert sent.status_code == 201 and sent.json()["message"].startswith("Your issue has been passed to the admin")
    again = client.post(url, headers=chat.headers, json={"summary": PROBLEM})
    assert again.status_code == 409 and again.json()["code"] == "HELP_ALREADY_OPEN"
    assert chat.transcript()[-1]["text"].startswith("Your issue has been passed to the admin")

    # Nobody can send another customer's chat.
    from tests.conftest import auth_headers

    stranger = auth_headers(client, _stranger(db).email)
    client.post("/api/chat/subscription", headers=stranger)
    assert client.post(url, headers=stranger, json={"summary": PROBLEM}).status_code == 404


def _stranger(db):
    from tests.conftest import Factory

    return Factory(db).user("stranger@test.io", name="Stranger")


# ---------------------------------------------------------------- the admin side


def test_admins_see_only_the_chats_sent_to_them_and_every_opening_is_audited(client, db, user, chat, staff) -> None:
    from app.models import AuditLog
    from tests.conftest import auth_headers

    admin, _, staff_member = staff
    admin_headers = auth_headers(client, admin.email)
    reply_of(chat.send("Hi", say("Hello! How can I help?")))
    # A chat nobody sent: invisible.
    assert client.get("/api/admin/help-requests", headers=admin_headers).json() == []

    request = _send(db, user, chat)
    reply_of(chat.send("Any news?", say("The admin will contact you soon.")))

    # Customers and staff without the permission can't read them.
    assert client.get("/api/admin/help-requests", headers=chat.headers).status_code == 403
    assert client.get("/api/admin/help-requests", headers=auth_headers(client, staff_member.email)).status_code == 403

    [listed] = client.get("/api/admin/help-requests", headers=admin_headers).json()
    assert listed["summary"] == PROBLEM and listed["customer"]["email"] == user.email and listed["overdue"] is False
    assert client.get("/api/admin/help-requests/count", headers=admin_headers).json() == {"open": 1, "overdue": 0}

    detail = client.get(f"/api/admin/help-requests/{request.id}", headers=admin_headers).json()
    assert [i["text"] for i in detail["transcript"]] == ["Hi", "Hello! How can I help?"]
    assert [i["text"] for i in detail["since"]][-2:] == ["Any news?", "The admin will contact you soon."]
    assert detail["since"][0]["text"].startswith("Your issue has been passed to the admin")
    assert detail["bookings"] == []
    db.expire_all()
    [viewed] = db.scalars(select(AuditLog).where(AuditLog.action == "CHAT_VIEWED")).all()
    assert viewed.actor_id == admin.id and str(viewed.entity_id) == str(request.id)
    assert client.get(f"/api/admin/help-requests/{uuid.uuid4()}", headers=admin_headers).status_code == 404

    freeze_time(NOW + timedelta(hours=7))
    assert client.get("/api/admin/help-requests/count", headers=admin_headers).json() == {"open": 1, "overdue": 1}
    resolved = client.post(f"/api/admin/help-requests/{request.id}/resolve", headers=admin_headers, json={"note": "Called them; refunded."})
    assert resolved.json()["status"] == "RESOLVED" and resolved.json()["resolved_by"] == "Admin"
    assert client.get("/api/admin/help-requests", headers=admin_headers).json() == []
    [done] = client.get("/api/admin/help-requests", headers=admin_headers, params={"status": "resolved"}).json()
    assert done["resolution_note"] == "Called them; refunded."
    assert client.post(f"/api/admin/help-requests/{request.id}/resolve", headers=admin_headers, json={}).status_code == 409


# ---------------------------------------------------------------- the customer says what went wrong


def test_the_assistant_must_ask_what_the_problem_is_first(db, user, chat, staff) -> None:
    from tests.chat_fakes import last_tool_results

    for vague in ["contact admin", "Customer wants to contact admin", "I want to talk to someone"]:
        assert help.is_only_a_request_for_contact(vague)
        reply = reply_of(chat.send("contact admin", call("contact_admin", {"summary": vague}), say("What happened?")))
        assert reply["cards"] == []
        assert last_tool_results(chat.model.requests[-1]["messages"])[0]["error"] == "NEEDS_DESCRIPTION"
    assert not help.is_only_a_request_for_contact("I was charged twice and want to talk to a person")
    # The same goes for a customer's own description.
    with pytest.raises(AppError) as vague:
        _send(db, user, chat, "Please let me talk to the admin")
    assert vague.value.code == "HELP_DESCRIPTION" and "what went wrong" in vague.value.message


def test_the_customer_checks_the_text_and_nothing_is_claimed_as_sent(db, user, chat, staff) -> None:
    steps = [call("contact_admin", {"summary": PROBLEM}), say("I've sent your message to the admin.")]
    reply = reply_of(chat.send("I was charged twice", *steps))
    # Whatever the model said, the reply says nothing has been sent yet.
    assert reply["text"].startswith("Tell the admin what happened in the box below") and "Nothing is sent until you press Send" in reply["text"]
    [card] = reply["cards"]

    # Too short: refused, and the card stays open to fix it.
    short = chat.client.post(f"/api/chat/proposals/{card['id']}/confirm", headers=chat.headers, json={"summary": "charged"})
    assert short.status_code == 422 and short.json()["code"] == "HELP_DESCRIPTION"
    assert db.scalars(select(ChatHelpRequest)).all() == []

    edited = "I was charged twice (2 x 40.00) for my Tuesday 10 AM session with John."
    sent = chat.client.post(f"/api/chat/proposals/{card['id']}/confirm", headers=chat.headers, json={"summary": edited})
    assert sent.json()["status"] == "CONFIRMED" and sent.json()["summary"] == edited
    [request] = db.scalars(select(ChatHelpRequest)).all()
    assert request.summary == edited
