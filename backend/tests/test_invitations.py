import logging
import re

from sqlalchemy import select

from app.models import Booking, BookingInvitation, Notification, User
from app.models.enums import InvitationStatus, NotificationChannel, NotificationType
from tests.conftest import MONDAY, at, auth_headers

GUEST = "guest@example.com"


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


def test_inviting_an_unregistered_email_sends_a_link_and_books_nothing(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        response = invite(client, admin_headers, service, resource, email="Guest@Example.com")
    assert response.status_code == 201, response.text
    assert response.json()["email"] == GUEST
    assert f"EMAIL to={GUEST}" in caplog.text and "/invitation?token=" in caplog.text
    assert db.scalars(select(Booking)).all() == []
    assert db.scalar(select(User).where(User.email == GUEST)) is None


def test_inviting_a_registered_email_is_refused(client, user, basic, admin_headers) -> None:
    service, resource = basic
    response = invite(client, admin_headers, service, resource, email=user.email)
    assert response.status_code == 409 and response.json()["code"] == "ALREADY_REGISTERED"


def test_only_staff_can_invite(client, basic, user_headers) -> None:
    service, resource = basic
    assert invite(client, user_headers, service, resource).status_code == 403


def test_accepting_registers_the_invitee_and_books_for_them(client, db, basic, admin, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)

    details = client.get(f"/api/invitations/{token}").json()
    assert details["status"] == "PENDING" and details["has_account"] is False
    assert details["invited_by"] == "Admin" and details["service_name"] == service.name

    accepted = client.post(
        f"/api/invitations/{token}/accept", json={"name": "Gus Guest", "password": "password123"}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["signed_in"] is True and accepted.json()["booking_id"]

    guest = db.scalar(select(User).where(User.email == GUEST))
    booking = db.scalars(select(Booking)).one()
    assert booking.user_id == guest.id
    # The new account can sign in, and the admin is told.
    assert client.post("/api/auth/login", json={"email": GUEST, "password": "password123"}).status_code == 200
    told = db.scalars(
        select(Notification).where(Notification.user_id == admin.id, Notification.type == NotificationType.INVITATION_ACCEPTED)
    ).all()
    assert told and all(n.read_at is None for n in told)
    # The link works once.
    client.cookies.clear()
    again = client.post(f"/api/invitations/{token}/accept", json={"name": "Gus", "password": "password123"})
    assert again.status_code == 410


def test_accepting_needs_a_name_and_password_for_a_new_account(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    response = client.post(f"/api/invitations/{token_from(caplog)}/accept", json={})
    assert response.status_code == 422 and response.json()["code"] == "DETAILS_REQUIRED"
    assert db.scalar(select(User).where(User.email == GUEST)) is None


def test_declining_tells_the_admin_and_creates_nothing(client, db, basic, admin, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)

    assert client.post(f"/api/invitations/{token}/decline").status_code == 200

    assert db.scalars(select(Booking)).all() == [] and db.scalar(select(User).where(User.email == GUEST)) is None
    note = db.scalars(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.type == NotificationType.INVITATION_DECLINED,
            Notification.channel == NotificationChannel.IN_APP,
        )
    ).one()
    assert note.title == "Booking rejected" and GUEST in note.body
    # The admin's console can fetch exactly these.
    listed = client.get("/api/notifications", headers=admin_headers, params={"type": "INVITATION_DECLINED", "unread_only": True})
    assert [n["id"] for n in listed.json()] == [str(note.id)]
    # A declined invitation can no longer be accepted.
    accept = client.post(f"/api/invitations/{token}/accept", json={"name": "Gus", "password": "password123"})
    assert accept.status_code == 410


def test_an_expired_invitation_cannot_be_used(client, db, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)
    db.execute(BookingInvitation.__table__.update().values(expires_at=BookingInvitation.created_at))
    db.commit()
    assert client.get(f"/api/invitations/{token}").json()["status"] == "EXPIRED"
    assert client.post(f"/api/invitations/{token}/decline").status_code == 410
    assert client.post(f"/api/invitations/{token}/accept", json={"name": "G", "password": "password123"}).status_code == 410


def test_unknown_token_is_a_404(client) -> None:
    assert client.get("/api/invitations/not-a-real-token").status_code == 404


def test_accepting_when_the_time_has_been_taken_keeps_the_account_and_tells_the_admin(
    client, db, basic, user_headers, admin, admin_headers, caplog
) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    # Someone else books the invited slot in the meantime.
    taken = client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)},
    )
    assert taken.status_code == 201, taken.text

    accepted = client.post(
        f"/api/invitations/{token_from(caplog)}/accept", json={"name": "Gus Guest", "password": "password123"}
    )

    assert accepted.status_code == 200
    assert accepted.json()["booking_id"] is None and accepted.json()["error"] and accepted.json()["signed_in"] is True
    assert db.scalar(select(User).where(User.email == GUEST)) is not None
    assert db.scalar(select(BookingInvitation)).status == InvitationStatus.FAILED
    note = db.scalars(
        select(Notification).where(
            Notification.user_id == admin.id,
            Notification.type == NotificationType.INVITATION_ACCEPTED,
            Notification.channel == NotificationChannel.IN_APP,
        )
    ).one()
    assert "could not be booked" in note.body


def test_an_account_made_after_the_invite_must_sign_in_to_accept(client, db, make, basic, admin_headers, caplog) -> None:
    service, resource = basic
    with caplog.at_level(logging.INFO, logger="notifications"):
        invite(client, admin_headers, service, resource)
    token = token_from(caplog)
    make.user(GUEST, name="Gus")

    assert client.get(f"/api/invitations/{token}").json()["has_account"] is True
    anonymous = client.post(f"/api/invitations/{token}/accept", json={})
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "SIGN_IN_REQUIRED"

    accepted = client.post(f"/api/invitations/{token}/accept", headers=auth_headers(client, GUEST), json={})
    assert accepted.status_code == 200 and accepted.json()["booking_id"] and accepted.json()["signed_in"] is False
