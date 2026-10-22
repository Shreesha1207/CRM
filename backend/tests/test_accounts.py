"""Nobody but the account's owner ever chooses its password."""

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.timeutils import freeze_time
from app.models import AuditLog, Booking, User
from app.models.enums import Role, UserStatus
from tests.conftest import MONDAY, NOW, PASSWORD, at, auth_headers


@pytest.fixture
def mails(monkeypatch) -> list[tuple[str, str, str]]:
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr("app.services.accounts.send_email", lambda to, subject, body, **kw: sent.append((to, subject, body)))
    return sent


def link_token(body: str) -> str:
    return re.search(r"token=([\w-]+)", body).group(1)


def test_a_new_account_is_set_up_by_its_owner(client, db, admin_headers, mails) -> None:
    r = client.post("/api/admin/users", headers=admin_headers, json={"name": "Nia", "email": "nia@test.io", "password": "chosen-by-admin"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "INVITED"
    # The admin's password (if sent at all) is ignored: nobody can sign in with it.
    assert client.post("/api/auth/login", json={"email": "nia@test.io", "password": "chosen-by-admin"}).status_code == 401

    (to, subject, body), = mails
    assert to == "nia@test.io" and "Set up your" in subject and "setup=1" in body
    r = client.post("/api/auth/reset-password", json={"token": link_token(body), "password": "nias-own-pass"})
    assert r.status_code == 200 and "ready" in r.json()["message"]
    assert db.get(User, client.post("/api/auth/login", json={"email": "nia@test.io", "password": "nias-own-pass"}).json()["user"]["id"]).status == UserStatus.ACTIVE


def test_an_invited_account_cannot_sign_in_until_it_is_set_up(client, admin_headers, mails) -> None:
    client.post("/api/admin/users", headers=admin_headers, json={"name": "Nia", "email": "nia@test.io"})
    r = client.post("/api/auth/login", json={"email": "nia@test.io", "password": "anything-at-all"})
    assert r.status_code in (401, 403)


def test_an_admin_cannot_set_or_change_a_users_password(client, user, admin_headers, mails) -> None:
    r = client.put(f"/api/admin/users/{user.id}", headers=admin_headers, json={"name": "Renamed", "password": "taken-over-1"})
    assert r.status_code == 200
    # The owner's password still works; the admin's does not.
    assert client.post("/api/auth/login", json={"email": user.email, "password": "taken-over-1"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 200


def test_an_admin_can_only_send_a_reset_link(client, user, admin_headers, mails) -> None:
    sessions = auth_headers(client, user.email)
    first = client.post(f"/api/admin/users/{user.id}/send-password-link", headers=admin_headers)
    assert first.status_code == 200 and user.email in first.json()["message"]
    second = client.post(f"/api/admin/users/{user.id}/send-password-link", headers=admin_headers)
    assert second.status_code == 200
    (to, subject, old_link), (_, _, new_link) = mails
    assert to == user.email and subject == "Reset your password"
    # Sending a link changes nothing by itself: the user is not locked out or signed out.
    assert client.get("/api/auth/me", headers=sessions).status_code == 200
    assert client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 200
    # Only the newest link works, and only the user can use it to pick the password.
    assert client.post("/api/auth/reset-password", json={"token": link_token(old_link), "password": "x-new-pass"}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": link_token(new_link), "password": "my-new-pass"}).status_code == 200


def test_resending_an_invitation_replaces_the_link(client, admin_headers, mails) -> None:
    made = client.post("/api/admin/users", headers=admin_headers, json={"name": "Nia", "email": "nia@test.io"}).json()
    resent = client.post(f"/api/admin/users/{made['id']}/send-password-link", headers=admin_headers)
    assert resent.status_code == 200 and "set up" in resent.json()["message"]
    (_, _, first), (_, _, second) = mails
    assert client.post("/api/auth/reset-password", json={"token": link_token(first), "password": "nias-own-pass"}).status_code == 400
    assert client.post("/api/auth/reset-password", json={"token": link_token(second), "password": "nias-own-pass"}).status_code == 200


def test_no_password_link_for_a_suspended_account(client, user, admin_headers, mails) -> None:
    client.put(f"/api/admin/users/{user.id}", headers=admin_headers, json={"status": "SUSPENDED"})
    r = client.post(f"/api/admin/users/{user.id}/send-password-link", headers=admin_headers)
    assert r.status_code == 409 and mails == []


# ------------------------------------------------------------------ deleting accounts


def test_anyone_can_delete_their_own_account(client, db, make, basic, user, user_headers, mails) -> None:
    service, resource = basic
    user_id, email, name = user.id, user.email, user.name
    make.settings(cancellation_window=60 * 24 * 7)  # too late to cancel online...
    booked = client.post(
        "/api/bookings", headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)},
    )
    assert booked.status_code == 201, booked.text
    assert client.post(f"/api/bookings/{booked.json()['id']}/cancel", headers=user_headers, json={}).status_code == 409

    assert client.post("/api/auth/delete-account", headers=user_headers, json={"password": "wrong-password"}).status_code == 400
    r = client.post("/api/auth/delete-account", headers=user_headers, json={"password": PASSWORD})
    assert r.status_code == 200, r.text

    # ...but deleting the account cancels it anyway, frees the time and erases everything.
    db.expire_all()
    assert db.get(User, user_id) is None
    assert db.scalars(select(Booking)).all() == []
    assert client.get("/api/auth/me", headers=user_headers).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401
    free = client.get("/api/availability", params={"service_id": str(service.id), "resource_id": str(resource.id), "date": MONDAY.isoformat()}).json()
    assert next(s for s in free["resources"][0]["slots"] if s["start_time"] == "10:00")["available"] is True
    # Nothing that identifies them stays in the audit log; they get a confirmation.
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "ACCOUNT_DELETED_BY_OWNER"))
    assert email not in str(entry.old_value) and name not in str(entry.old_value)
    assert [m[0] for m in mails] == [email] and "deleted" in mails[0][1]


def test_an_account_waits_for_a_booking_in_progress_to_end(client, basic, user, user_headers, mails) -> None:
    service, resource = basic
    client.post("/api/bookings", headers=user_headers, json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)})
    freeze_time(NOW.replace(day=1, month=3, hour=10, minute=30))
    user_headers = auth_headers(client, user.email)
    r = client.post("/api/auth/delete-account", headers=user_headers, json={"password": PASSWORD})
    assert r.status_code == 409 and r.json()["code"] == "BOOKING_IN_PROGRESS"
    freeze_time(NOW.replace(day=1, month=3, hour=11))
    assert client.post("/api/auth/delete-account", headers=user_headers, json={"password": PASSWORD}).status_code == 200


def test_the_last_super_admin_cannot_delete_their_account(client, make) -> None:
    make.user("boss@test.io", Role.SUPER_ADMIN, name="Boss")
    headers = auth_headers(client, "boss@test.io")
    r = client.post("/api/auth/delete-account", headers=headers, json={"password": PASSWORD})
    assert r.status_code == 409 and r.json()["code"] == "LAST_SUPER_ADMIN"


def test_an_admin_deletes_a_customer_only_after_a_year_inactive(client, db, make, user, admin_headers) -> None:
    def listed():
        return next(u for u in client.get("/api/admin/users", headers=admin_headers).json()["items"] if u["id"] == str(user.id))

    # Active customers are not the admin's to delete.
    assert client.delete(f"/api/admin/users/{user.id}", headers=admin_headers).status_code == 403
    assert listed()["can_delete"] is False and listed()["deletable_from"] is None

    client.put(f"/api/admin/users/{user.id}", headers=admin_headers, json={"status": "INACTIVE"})
    refused = client.delete(f"/api/admin/users/{user.id}", headers=admin_headers)
    assert refused.status_code == 403 and refused.json()["code"] == "NOT_DELETABLE_YET"
    assert listed()["deletable_from"].startswith(str((NOW + timedelta(days=365)).date()))

    freeze_time(NOW + timedelta(days=366))
    admin_headers = auth_headers(client, "admin@test.io")  # the old session has expired by now
    assert listed()["can_delete"] is True
    user_id = user.id
    assert client.delete(f"/api/admin/users/{user_id}", headers=admin_headers).status_code == 204
    db.expire_all()
    assert db.get(User, user_id) is None


def test_admins_never_delete_accounts_when_the_setting_is_zero(client, make, user, admin_headers) -> None:
    make.settings(inactive_account_deletion_days=0)
    client.put(f"/api/admin/users/{user.id}", headers=admin_headers, json={"status": "INACTIVE"})
    freeze_time(NOW + timedelta(days=3650))
    admin_headers = auth_headers(client, "admin@test.io")
    assert client.delete(f"/api/admin/users/{user.id}", headers=admin_headers).status_code == 403


# ------------------------------------------------------------------ phone numbers


@pytest.mark.parametrize("phone", ["call me maybe", "12345", "+1 555 0100 2233 99887", "98765 43210 ext. 2", "<script>"])
def test_a_phone_number_must_look_like_one(client, user, user_headers, admin_headers, phone) -> None:
    for method, path, headers, extra in (
        ("post", "/api/auth/register", {}, {"name": "A", "email": "new@test.io", "password": "longpassword"}),
        ("put", "/api/auth/me", user_headers, {}),
        ("put", f"/api/admin/users/{user.id}", admin_headers, {}),
        ("post", "/api/admin/users", admin_headers, {"name": "B", "email": "b@test.io"}),
    ):
        r = getattr(client, method)(path, headers=headers, json={**extra, "phone": phone})
        assert r.status_code == 422, (path, r.text)
        assert "phone" in str(r.json()["detail"][0]["loc"])


def test_phone_numbers_are_stored_without_spacing(client, user_headers) -> None:
    r = client.put("/api/auth/me", headers=user_headers, json={"phone": " +91 98765-43210 "})
    assert r.status_code == 200 and r.json()["phone"] == "+919876543210"
    # Clearing it is fine too.
    assert client.put("/api/auth/me", headers=user_headers, json={"phone": ""}).json()["phone"] is None
