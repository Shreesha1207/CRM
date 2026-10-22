"""Nobody but the account's owner ever chooses its password."""

import re

import pytest

from app.models import User
from app.models.enums import UserStatus
from tests.conftest import PASSWORD, auth_headers


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
