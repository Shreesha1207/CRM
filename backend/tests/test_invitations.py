import logging
import re

from sqlalchemy import select

from app.core.security import hash_password, hash_token
from app.models import Booking, BookingInvitation, Notification, User
from app.models.enums import BookingStatus, InvitationStatus, NotificationChannel, NotificationType, UserStatus
from app.services import invitations as invitation_service
from tests.conftest import MONDAY, PASSWORD, at, auth_headers

GUEST = "guest@example.com"
ACTIVE = ["PENDING", "CONFIRMED", "CONFLICTED"]  # what the calendar's "Active bookings" filter asks for


def invite(client, headers, service, resource, start=None, email=GUEST, **extra):
    return client.post(
        "/api/admin/bookings/invite",
        headers=headers,
        json={
            "service_id": str(service.id),
            "resource_id": str(resource.id),
            "start": start or at(MONDAY, 10),
            "customer_email": email,
            **extra,
        },
    )


def token_from(caplog) -> str:
    return re.search(r"/invitation\?token=(\S+)", caplog.text).group(1)


def on_calendar(client, headers) -> list[str]:
    """Ids of the bookings the admin calendar shows for that Monday."""
    r = client.get("/api/admin/calendar", headers=headers, params={"start": str(MONDAY), "end": str(MONDAY), "status": ACTIVE})
    assert r.status_code == 200, r.text
    return [b["id"] for b in r.json()["bookings"]]


def book_slot(client, headers, service, resource, start):
    return client.post(
        "/api/bookings", headers=headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": start},
    )


# ------------------------------------------------------------------ inviting someone new


def test_inviting_a_new_email_puts_the_booking_on_the_calendar_and_sends_a_link(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        response = invite(client, admin_headers, service, resource, email="Guest@Example.com")
    assert response.status_code == 201, response.text
    assert response.json()["email"] == GUEST
    assert f"EMAIL to={GUEST}" in caplog.text and "/invitation?token=" in caplog.text
    assert "It is on the calendar for you now" in caplog.text

    # The booking is there at once, pending, under a placeholder account.
    booking = db.scalars(select(Booking)).one()
    guest = db.scalar(select(User).where(User.email == GUEST))
    assert booking.user_id == guest.id and booking.status == BookingStatus.PENDING
    assert guest.status == UserStatus.INVITED
    assert on_calendar(client, admin_headers) == [str(booking.id)]
    # ... and it holds the time.
    assert all(a.active for a in booking.allocations)


def test_the_invited_time_is_held_even_when_pending_bookings_normally_are_not(client, db, basic, admin_headers, user_headers) -> None:
    service, resource = basic
    client.put("/api/admin/settings", headers=admin_headers, json={"values": {"pending_consumes_capacity": False}})
    assert invite(client, admin_headers, service, resource).status_code == 201
    assert book_slot(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 409


def test_the_placeholder_account_is_never_written_to_and_cannot_sign_in(client, db, basic, admin_headers) -> None:
    service, resource = basic
    invite(client, admin_headers, service, resource)
    # No e-mails or notices to an address nobody has claimed, apart from the invitation itself.
    guest = db.scalar(select(User).where(User.email == GUEST))
    assert db.scalars(select(Notification).where(Notification.user_id == guest.id)).all() == []
    assert client.post("/api/auth/login", json={"email": GUEST, "password": "anything-at-all"}).status_code == 401
    # Registering normally with that e-mail points them to the invitation.
    r = client.post("/api/auth/register", json={"name": "Gus", "email": GUEST, "password": "password123"})
    assert r.status_code == 409 and "invitation" in r.json()["detail"]
    assert client.post("/api/auth/forgot-password", json={"email": GUEST}).status_code == 202
    assert db.scalar(select(User).where(User.email == GUEST)).status == UserStatus.INVITED


def test_only_staff_can_invite(client, basic, user_headers) -> None:
    service, resource = basic
    assert invite(client, user_headers, service, resource).status_code == 403


def test_inviting_an_email_that_has_an_account_is_refused(client, user, basic, admin_headers) -> None:
    service, resource = basic
    response = invite(client, admin_headers, service, resource, email=user.email)
    assert response.status_code == 409 and response.json()["code"] == "ALREADY_REGISTERED"


def test_a_taken_time_is_refused_and_leaves_nothing_behind(client, db, basic, admin_headers, user_headers) -> None:
    service, resource = basic
    assert book_slot(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201
    response = invite(client, admin_headers, service, resource, start=at(MONDAY, 10))
    assert response.status_code == 409 and response.json()["code"] == "CONFLICT"
    assert db.scalar(select(User).where(User.email == GUEST)) is None
    assert db.scalars(select(BookingInvitation)).all() == []


def test_if_the_email_cannot_be_sent_the_booking_comes_off_the_calendar_again(client, db, basic, admin_headers, user_headers, monkeypatch) -> None:
    service, resource = basic

    def broken(*args, **kwargs):
        raise OSError("mail server down")

    monkeypatch.setattr("app.services.invitations.send_email", broken)
    response = invite(client, admin_headers, service, resource)
    assert response.status_code == 502 and response.json()["code"] == "EMAIL_FAILED"
    assert db.scalar(select(Booking)).status == BookingStatus.CANCELLED
    assert on_calendar(client, admin_headers) == []
    assert db.scalar(select(BookingInvitation)).status == InvitationStatus.FAILED
    # The time is free again.
    assert book_slot(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201


def test_an_admin_cannot_book_directly_for_a_customer_who_has_not_registered(client, db, basic, admin_headers) -> None:
    service, resource = basic
    invite(client, admin_headers, service, resource)
    guest = db.scalar(select(User).where(User.email == GUEST))
    response = client.post(
        "/api/admin/bookings", headers=admin_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 12), "user_id": str(guest.id)},
    )
    assert response.status_code == 409 and response.json()["code"] == "NOT_REGISTERED"


# ------------------------------------------------------------------ accepting


def test_accepting_registers_the_invitee_and_confirms_their_booking(client, db, basic, admin, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)
    booking_id = db.scalar(select(Booking.id))

    details = client.get(f"/api/invitations/{token}").json()
    assert details["status"] == "PENDING" and details["has_account"] is False and details["booked"] is True
    assert details["invited_by"] == "Admin" and details["service_name"] == service.name

    accepted = client.post(f"/api/invitations/{token}/accept", json={"name": "Gus Guest", "password": "password123"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["signed_in"] is True and accepted.json()["booking_id"] == str(booking_id)

    db.expire_all()
    guest = db.scalar(select(User).where(User.email == GUEST))
    booking = db.scalars(select(Booking)).one()
    assert booking.id == booking_id and booking.user_id == guest.id and booking.status == BookingStatus.CONFIRMED
    assert guest.status == UserStatus.ACTIVE and guest.name == "Gus Guest"
    assert client.post("/api/auth/login", json={"email": GUEST, "password": "password123"}).status_code == 200
    # Now they are an account like any other: they get the booking confirmation, and the admin is told.
    mine = db.scalars(select(Notification).where(Notification.user_id == guest.id)).all()
    assert {(n.type, n.channel) for n in mine} == {
        (NotificationType.BOOKING_CONFIRMED, NotificationChannel.IN_APP),
        (NotificationType.BOOKING_CONFIRMED, NotificationChannel.EMAIL),
    }
    told = db.scalars(
        select(Notification).where(Notification.user_id == admin.id, Notification.type == NotificationType.INVITATION_ACCEPTED)
    ).all()
    assert told and all(n.read_at is None for n in told)
    # The link works once.
    client.cookies.clear()
    again = client.post(f"/api/invitations/{token}/accept", json={"name": "Gus", "password": "password123"})
    assert again.status_code == 410


def test_accepting_needs_a_name_and_password(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    response = client.post(f"/api/invitations/{token_from(caplog)}/accept", json={})
    assert response.status_code == 422 and response.json()["code"] == "DETAILS_REQUIRED"
    assert db.scalar(select(User).where(User.email == GUEST)).status == UserStatus.INVITED
    assert db.scalar(select(Booking)).status == BookingStatus.PENDING


def test_accepting_after_the_admin_cancelled_the_booking_tells_the_admin(client, db, basic, admin, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    booking_id = db.scalar(select(Booking.id))
    assert client.post(f"/api/admin/bookings/{booking_id}/cancel", headers=admin_headers, json={}).status_code == 200

    accepted = client.post(f"/api/invitations/{token_from(caplog)}/accept", json={"name": "Gus Guest", "password": "password123"})

    assert accepted.status_code == 200
    assert accepted.json()["booking_id"] is None and accepted.json()["error"] and accepted.json()["signed_in"] is True
    assert db.scalar(select(User).where(User.email == GUEST)).status == UserStatus.ACTIVE  # the account is kept
    assert db.scalar(select(BookingInvitation)).status == InvitationStatus.FAILED
    note = db.scalars(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.type == NotificationType.INVITATION_ACCEPTED,
            Notification.channel == NotificationChannel.IN_APP,
        )
    ).one()
    assert "could not be confirmed" in note.body


def test_an_activated_account_must_sign_in_to_accept(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)
    guest = db.scalar(select(User).where(User.email == GUEST))
    guest.status, guest.password_hash = UserStatus.ACTIVE, hash_password(PASSWORD)
    db.commit()

    assert client.get(f"/api/invitations/{token}").json()["has_account"] is True
    anonymous = client.post(f"/api/invitations/{token}/accept", json={})
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "SIGN_IN_REQUIRED"

    accepted = client.post(f"/api/invitations/{token}/accept", headers=auth_headers(client, GUEST), json={})
    assert accepted.status_code == 200 and accepted.json()["booking_id"] and accepted.json()["signed_in"] is False
    assert db.scalar(select(Booking)).status == BookingStatus.CONFIRMED


# ------------------------------------------------------------------ declining


def test_declining_takes_the_booking_off_the_calendar_and_tells_the_admin(client, db, basic, admin, admin_headers, user_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)
    assert len(on_calendar(client, admin_headers)) == 1

    assert client.post(f"/api/invitations/{token}/decline").status_code == 200

    booking = db.scalar(select(Booking))
    assert booking.status == BookingStatus.CANCELLED and booking.cancellation_reason == "Declined by the customer"
    assert on_calendar(client, admin_headers) == []
    assert db.scalar(select(BookingInvitation)).status == InvitationStatus.DECLINED
    # The time is free for someone else again.
    assert book_slot(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201
    note = db.scalars(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.type == NotificationType.INVITATION_DECLINED,
            Notification.channel == NotificationChannel.IN_APP,
        )
    ).one()
    assert note.title == "Booking rejected" and GUEST in note.body and "removed from the calendar" in note.body
    # The admin's console can fetch exactly these.
    listed = client.get("/api/notifications", headers=admin_headers, params={"type": "INVITATION_DECLINED", "unread_only": True})
    assert [n["id"] for n in listed.json()] == [str(note.id)]
    # Nobody is e-mailed at the unclaimed address about the cancellation they just asked for.
    guest = db.scalar(select(User).where(User.email == GUEST))
    assert db.scalars(select(Notification).where(Notification.user_id == guest.id)).all() == []
    # A declined invitation can no longer be accepted.
    accept = client.post(f"/api/invitations/{token}/accept", json={"name": "Gus", "password": "password123"})
    assert accept.status_code == 410


def test_inviting_the_same_email_again_reuses_the_placeholder(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource, start=at(MONDAY, 10))
    client.post(f"/api/invitations/{token_from(caplog)}/decline")
    assert invite(client, admin_headers, service, resource, start=at(MONDAY, 12)).status_code == 201
    assert len(db.scalars(select(User).where(User.email == GUEST)).all()) == 1
    assert len(on_calendar(client, admin_headers)) == 1


# ------------------------------------------------------------------ not answering


def test_an_expired_invitation_cannot_be_used_but_the_booking_stays(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)
    db.execute(BookingInvitation.__table__.update().values(expires_at=BookingInvitation.created_at))
    db.commit()
    assert client.get(f"/api/invitations/{token}").json()["status"] == "EXPIRED"
    assert client.post(f"/api/invitations/{token}/decline").status_code == 410
    assert client.post(f"/api/invitations/{token}/accept", json={"name": "G", "password": "password123"}).status_code == 410
    # Only a decline removes it: with no answer the booking stays where it is.
    assert db.scalar(select(Booking)).status == BookingStatus.PENDING
    assert len(on_calendar(client, admin_headers)) == 1


def test_unknown_token_is_a_404(client) -> None:
    assert client.get("/api/invitations/not-a-real-token").status_code == 404


# ------------------------------------------------------------------ invitations from before bookings were held


def test_an_older_invitation_without_a_booking_still_books_when_accepted(client, db, basic, admin) -> None:
    service, resource = basic
    token = "legacy-token-for-the-test"
    db.add(
        BookingInvitation(
            email=GUEST,
            token_hash=hash_token(token),
            invited_by_id=admin.id,
            request={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10), "quantity": 1},
            expires_at=invitation_service.expiry(),
        )
    )
    db.commit()
    assert db.scalars(select(Booking)).all() == []
    accepted = client.post(f"/api/invitations/{token}/accept", json={"name": "Gus Guest", "password": "password123"})
    assert accepted.status_code == 200 and accepted.json()["booking_id"]
    assert db.scalar(select(Booking)).user_id == db.scalar(select(User.id).where(User.email == GUEST))


# ------------------------------------------------------------------ customers who already have an account


def test_a_registered_customer_is_booked_directly_and_e_mailed_a_confirmation(client, db, admin, user, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        response = client.post(
            "/api/admin/bookings", headers=admin_headers,
            json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10), "user_id": str(user.id)},
        )
    assert response.status_code == 201, response.text
    booking = db.scalar(select(Booking))
    assert booking.user_id == user.id and booking.status == BookingStatus.CONFIRMED
    assert on_calendar(client, admin_headers) == [str(booking.id)]

    rows = db.scalars(select(Notification).where(Notification.user_id == user.id)).all()
    assert {n.channel for n in rows} == {NotificationChannel.IN_APP, NotificationChannel.EMAIL}
    assert all(n.type == NotificationType.BOOKING_CONFIRMED and "Booked for you by Admin" in n.body for n in rows)
    # The e-mail goes out right after the booking is made.
    assert f"EMAIL to={user.email}" in caplog.text and "Booking confirmed" in caplog.text
    assert "Booked for you by Admin" in caplog.text
    assert db.scalar(select(Notification.status).where(Notification.user_id == user.id, Notification.channel == NotificationChannel.EMAIL)) == "SENT"


def test_an_admin_cannot_hand_out_the_invited_status(client, db, basic, admin_headers) -> None:
    service, resource = basic
    invite(client, admin_headers, service, resource)
    guest = db.scalar(select(User).where(User.email == GUEST))
    # An invited customer can still be edited (the screen sends the status back unchanged) ...
    edited = client.put(f"/api/admin/users/{guest.id}", headers=admin_headers, json={"name": "Gus G", "status": "INVITED"})
    assert edited.status_code == 200, edited.text
    # ... but nobody else can be turned into one, or created as one.
    ok = client.post("/api/admin/users", headers=admin_headers, json={"name": "A", "email": "a@example.com", "password": "password123"}).json()
    assert client.put(f"/api/admin/users/{ok['id']}", headers=admin_headers, json={"status": "INVITED"}).status_code == 422
    made = client.post("/api/admin/users", headers=admin_headers, json={"name": "B", "email": "b@example.com", "password": "password123", "status": "INVITED"})
    assert made.status_code == 422
