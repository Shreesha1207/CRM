import re

from sqlalchemy import text

from app.db.session import engine
from app.jobs import runner
from app.services import notifications
from tests.conftest import MONDAY, PASSWORD, at, auth_headers


def test_register_login_me_logout(client) -> None:
    r = client.post(
        "/api/auth/register",
        json={"name": "Ann", "email": "Ann@Example.com", "password": "longpassword", "role": "ADMIN"},
    )
    assert r.status_code == 201
    body = r.json()
    # A client cannot pick its own role.
    assert body["user"]["role"] == "USER" and body["user"]["email"] == "ann@example.com"
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client.cookies.clear()

    assert client.get("/api/auth/me", headers=headers).json()["permissions"] == []
    assert client.post("/api/auth/register", json={"name": "A", "email": "ann@example.com", "password": "longpassword"}).status_code == 409

    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    # The token is revoked server side, not just forgotten by the client.
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_passwords_are_hashed_and_bad_logins_rejected(client, user, db) -> None:
    assert user.password_hash.startswith("$argon2")
    assert PASSWORD not in user.password_hash
    r = client.post("/api/auth/login", json={"email": user.email, "password": "wrong-password"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "nobody@test.io", "password": "wrong-password"})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid e-mail or password"


def test_login_rate_limited(client, user) -> None:
    codes = [client.post("/api/auth/login", json={"email": user.email, "password": "nope-nope"}).status_code for _ in range(11)]
    assert codes[-1] == 429


def test_cookie_auth_requires_csrf_token_for_writes(client, user) -> None:
    login = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    csrf = login.json()["csrf_token"]
    assert client.get("/api/auth/me").status_code == 200  # reads work with the cookie alone
    assert client.put("/api/auth/me", json={"name": "Changed"}).status_code == 403
    r = client.put("/api/auth/me", json={"name": "Changed"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["name"] == "Changed"


def test_forgot_and_reset_password(client, user, monkeypatch) -> None:
    sent = []
    monkeypatch.setattr("app.api.routes.auth.send_email", lambda to, subject, body: sent.append(body))
    assert client.post("/api/auth/forgot-password", json={"email": "unknown@test.io"}).status_code == 202
    assert sent == []  # same response, no e-mail, for unknown addresses
    client.post("/api/auth/forgot-password", json={"email": user.email})
    token = re.search(r"token=([\w-]+)", sent[0]).group(1)

    old_headers = auth_headers(client, user.email)
    r = client.post("/api/auth/reset-password", json={"token": token, "password": "brand-new-pass"})
    assert r.status_code == 200
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "another-pass"}).status_code == 400
    assert client.get("/api/auth/me", headers=old_headers).status_code == 401  # sessions revoked
    assert client.post("/api/auth/login", json={"email": user.email, "password": "brand-new-pass"}).status_code == 200


def test_users_cannot_touch_other_users_bookings(client, make, basic, user_headers) -> None:
    service, resource = basic
    other = make.user("other@test.io")
    other_headers = auth_headers(client, other.email)
    booking = client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)},
    ).json()

    assert client.get(f"/api/bookings/{booking['id']}", headers=other_headers).status_code == 404
    assert client.post(f"/api/bookings/{booking['id']}/cancel", headers=other_headers, json={}).status_code == 404
    assert client.post(f"/api/bookings/{booking['id']}/reschedule", headers=other_headers, json={"start": at(MONDAY, 12)}).status_code == 404
    assert client.get("/api/bookings", headers=other_headers).json()["total"] == 0


def test_users_cannot_reach_admin_endpoints_or_escalate(client, basic, user, user_headers) -> None:
    service, resource = basic
    for method, url in [
        ("get", "/api/admin/bookings"),
        ("get", "/api/admin/users"),
        ("get", "/api/admin/dashboard"),
        ("get", "/api/admin/settings"),
        ("post", "/api/admin/resources"),
        ("put", f"/api/admin/users/{user.id}"),
    ]:
        kwargs = {"json": {"name": "x", "role": "ADMIN"}} if method != "get" else {}
        r = getattr(client, method)(url, headers=user_headers, **kwargs)
        assert r.status_code == 403, (url, r.status_code)

    # Booking on behalf of someone else, or overriding rules, is refused.
    r = client.post(
        "/api/bookings/recurring",
        headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10), "override_rules": True},
    )
    assert r.status_code == 403


def test_unauthenticated_requests_are_rejected(client) -> None:
    assert client.get("/api/bookings").status_code == 401
    assert client.get("/api/admin/bookings").status_code == 401
    assert client.get("/api/bookings", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_security_headers_present(client) -> None:
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]


def test_reminders_are_never_queued_twice(client, db, basic, user_headers) -> None:
    from datetime import UTC, datetime

    service, resource = basic
    client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)},
    )
    now = datetime(2027, 3, 1, 9, 30, tzinfo=UTC)  # 30 minutes before the start
    assert notifications.queue_reminders(db, now) == 1
    notifications.queue_reminders(db, now)
    reminders = [
        n for n in client.get("/api/notifications", headers=user_headers).json() if n["type"] == "BOOKING_REMINDER"
    ]
    assert len(reminders) == 1 and "1 hour" in reminders[0]["title"]
    # Queued e-mails are delivered once and marked sent.
    assert notifications.dispatch_pending(db) >= 1
    assert notifications.dispatch_pending(db) == 0


def test_background_job_releases_its_lock() -> None:
    # A long-running API has several idle connections in its pool, so each
    # commit inside the job can leave the Session on a different connection.
    connections = [engine.connect() for _ in range(3)]
    for connection in connections:
        connection.close()

    runner.run_once()

    with engine.connect() as connection:
        holders = connection.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 AND objid = :id"
                " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
            ),
            {"id": runner.JOB_LOCK_ID},
        )
    # A leaked lock would stop every later run from doing any work.
    assert holders == 0


def test_session_endpoint_is_anonymous_friendly(client, user) -> None:
    assert client.get("/api/auth/session").json() == {"user": None}
    headers = auth_headers(client, user.email)
    assert client.get("/api/auth/session", headers=headers).json()["user"]["email"] == user.email
