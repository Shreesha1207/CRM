from datetime import datetime, time, timedelta

from sqlalchemy import select

from app.core.timeutils import freeze_time
from app.models import Notification
from app.models.enums import BookingType, NotificationChannel, NotificationType
from app.services import notifications
from tests.conftest import MONDAY, at


def book(client, headers, service, resource, start, **extra):
    return client.post(
        "/api/bookings",
        headers=headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": start, **extra},
    )


def block(client, headers, resource, start, end, *, dry_run=False, action="mark_conflicted", **extra):
    return client.post(
        "/api/admin/exceptions",
        headers=headers,
        params={"dry_run": dry_run, "conflict_action": action},
        json={"resource_id": str(resource.id), "start": start, "end": end, "type": "BLOCKED", **extra},
    )


def test_schedule_change_preview_then_mark_conflicted_then_resolve(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()

    preview = block(client, admin_headers, resource, at(MONDAY, 9), at(MONDAY, 12), dry_run=True).json()
    assert preview["applied"] is False
    assert preview["affected_count"] == 1
    assert preview["affected_bookings"][0]["booking_id"] == booking["id"]
    # A preview changes nothing.
    assert client.get("/api/admin/exceptions", headers=admin_headers).json() == []

    applied = block(client, admin_headers, resource, at(MONDAY, 9), at(MONDAY, 12), reason="Plumbing").json()
    assert applied["applied"] is True
    conflicted = client.get(f"/api/bookings/{booking['id']}", headers=user_headers).json()
    assert conflicted["status"] == "CONFLICTED" and "Plumbing" in conflicted["conflict_reason"]

    conflicts = client.get("/api/admin/conflicts", headers=admin_headers).json()
    assert conflicts["total"] == 1
    detail = client.get(f"/api/admin/conflicts/{booking['id']}", headers=admin_headers).json()
    assert detail["alternatives"], "expected alternative slots"

    resolved = client.post(
        f"/api/admin/conflicts/{booking['id']}/resolve", headers=admin_headers, json={"action": "override", "reason": "Fixed early"}
    ).json()
    assert resolved["status"] == "CONFIRMED"
    assert client.get("/api/admin/conflicts", headers=admin_headers).json()["total"] == 0


def test_resolve_conflict_by_rescheduling(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    block(client, admin_headers, resource, at(MONDAY, 9), at(MONDAY, 12))
    r = client.post(
        f"/api/admin/conflicts/{booking['id']}/resolve", headers=admin_headers, json={"action": "reschedule", "start": at(MONDAY, 14)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED" and r.json()["rescheduled_from_id"] == booking["id"]


def test_resolve_conflict_by_reassigning_resource(client, make, user_headers, admin_headers) -> None:
    service = make.service()
    a = make.resource("A", services=[service], hours=make.weekdays(time(9), time(17)))
    b = make.resource("B", services=[service], hours=make.weekdays(time(9), time(17)))
    booking = book(client, user_headers, service, a, at(MONDAY, 10)).json()
    block(client, admin_headers, a, at(MONDAY, 9), at(MONDAY, 12))
    r = client.post(
        f"/api/admin/conflicts/{booking['id']}/resolve", headers=admin_headers, json={"action": "reassign", "resource_id": str(b.id)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["resource"]["name"] == "B"
    notes = client.get("/api/notifications", headers=user_headers).json()
    assert any(n["type"] == "RESOURCE_CHANGED" for n in notes)


def test_schedule_change_cancel_action(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    rules = [{"day_of_week": d, "start_time": "13:00", "end_time": "17:00"} for d in range(5)]
    preview = client.put(
        f"/api/admin/resources/{resource.id}/availability", headers=admin_headers, params={"dry_run": True}, json=rules
    ).json()
    assert preview["affected_count"] == 1
    client.put(
        f"/api/admin/resources/{resource.id}/availability", headers=admin_headers, params={"conflict_action": "cancel"}, json=rules
    )
    cancelled = client.get(f"/api/bookings/{booking['id']}", headers=user_headers).json()
    assert cancelled["status"] == "CANCELLED" and cancelled["cancellation_reason"].startswith("Schedule change")


def test_keep_action_leaves_bookings_untouched(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    r = block(client, admin_headers, resource, at(MONDAY, 9), at(MONDAY, 12), action="keep").json()
    assert r["applied"] and r["affected_count"] == 1
    assert client.get(f"/api/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CONFIRMED"


def test_operating_hours_change_detects_affected_bookings(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    book(client, user_headers, service, resource, at(MONDAY, 16))
    hours = [{"day_of_week": d, "start_time": "09:00", "end_time": "15:00"} for d in range(5)]
    preview = client.put("/api/admin/operating-hours", headers=admin_headers, params={"dry_run": True}, json=hours).json()
    assert preview["affected_count"] == 1
    assert preview["affected_bookings"][0]["code"] == "OUTSIDE_OPERATING_HOURS"


def test_resource_with_history_is_deactivated_not_deleted(client, make, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    preview = client.delete(f"/api/admin/resources/{resource.id}", headers=admin_headers, params={"dry_run": True}).json()
    assert preview["affected_count"] == 1 and preview["applied"] is False

    result = client.delete(f"/api/admin/resources/{resource.id}", headers=admin_headers).json()
    assert result["result"] == {"deleted": False, "deactivated": True}
    assert client.get(f"/api/admin/resources/{resource.id}", headers=admin_headers).json()["status"] == "INACTIVE"
    assert client.get(f"/api/bookings/{booking['id']}", headers=user_headers).json()["status"] == "CONFLICTED"

    unused = make.resource("Unused")
    gone = client.delete(f"/api/admin/resources/{unused.id}", headers=admin_headers).json()
    assert gone["result"] == {"deleted": True}
    assert client.get(f"/api/admin/resources/{unused.id}", headers=admin_headers).status_code == 404


def test_admin_crud_for_catalogue(client, admin_headers) -> None:
    loc = client.post("/api/admin/locations", headers=admin_headers, json={"name": "Club", "timezone": "Europe/London"}).json()
    bad_tz = client.post("/api/admin/locations", headers=admin_headers, json={"name": "X", "timezone": "Mars/Base"})
    assert bad_tz.status_code == 422

    svc = client.post(
        "/api/admin/services",
        headers=admin_headers,
        json={"name": "Court hire", "duration_minutes": 60, "price": "20.00", "buffer_after": 10},
    ).json()
    res = client.post(
        "/api/admin/resources",
        headers=admin_headers,
        json={"name": "Court 1", "type": "COURT", "location_id": loc["id"], "metadata": {"surface": "clay"}},
    ).json()
    assert res["metadata"] == {"surface": "clay"} and res["timezone"] == "Europe/London"

    links = client.put(
        f"/api/admin/resources/{res['id']}/services", headers=admin_headers, json=[{"service_id": svc["id"], "custom_price": "25.00"}]
    ).json()
    assert links[0]["price"] == "25.00"

    public = client.get(f"/api/resources/{res['id']}").json()
    assert public["services"][0]["service_name"] == "Court hire"

    # A service without bookings is deleted; one with bookings would be deactivated.
    assert client.delete(f"/api/admin/services/{svc['id']}", headers=admin_headers).json()["deleted"] is True


def test_location_map_links(client, make, db, user_headers, admin_headers) -> None:
    locations = "/api/admin/locations"
    # Only web links: the link ends up in an href.
    bad = client.post(locations, headers=admin_headers, json={"name": "X", "map_url": "javascript:alert(1)"})
    assert bad.status_code == 422
    own = client.post(locations, headers=admin_headers, json={"name": "Club", "map_url": " https://maps.app.goo.gl/abc "}).json()
    assert own["map_url"] == own["google_maps_url"] == "https://maps.app.goo.gl/abc"
    assert client.post(locations, headers=admin_headers, json={"name": "Nowhere"}).json()["google_maps_url"] is None

    # Without a link of its own, a location links to a Maps search for its address.
    hq = make.location(name="HQ")
    hq.address = "1 Main St, Springfield"
    make.db.commit()
    search = "https://www.google.com/maps/search/?api=1&query=HQ%2C+1+Main+St%2C+Springfield"
    assert next(x for x in client.get("/api/locations").json() if x["name"] == "HQ")["google_maps_url"] == search

    service = make.service()
    resource = make.resource(services=[service], location=hq, hours=make.weekdays(time(9), time(17)))
    assert client.get(f"/api/services/{service.id}").json()["resources"][0]["location"]["google_maps_url"] == search
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    assert booking["location"] == {"id": str(hq.id), "name": "HQ", "address": "1 Main St, Springfield", "google_maps_url": search}

    # Messages say where; the e-mail also links to the map.
    [note] = client.get("/api/notifications", headers=user_headers).json()
    assert note["body"].endswith("\nWhere: HQ, 1 Main St, Springfield")
    email = db.scalars(select(Notification).where(Notification.channel == NotificationChannel.EMAIL)).one()
    assert email.body == f"{note['body']}\nMap: {search}"


def test_location_map_link_can_be_changed_and_cleared(client, admin_headers) -> None:
    loc = client.post("/api/admin/locations", headers=admin_headers, json={"name": "HQ", "address": "1 Main St"}).json()

    def update(map_url):
        body = {"name": "HQ", "address": "1 Main St", "timezone": "UTC", "map_url": map_url}
        r = client.put(f"/api/admin/locations/{loc['id']}", headers=admin_headers, json=body)
        assert r.status_code == 200, r.text
        return r.json()["result"]

    assert update("https://maps.app.goo.gl/xyz")["google_maps_url"] == "https://maps.app.goo.gl/xyz"
    cleared = update("  ")
    assert cleared["map_url"] is None
    assert cleared["google_maps_url"] == "https://www.google.com/maps/search/?api=1&query=HQ%2C+1+Main+St"
    # The audit trail records the link like any other field.
    [first, second] = client.get("/api/admin/audit-logs", headers=admin_headers, params={"action": "LOCATION_UPDATED"}).json()["items"]
    assert {first["new_value"]["old"]["map_url"], second["new_value"]["old"]["map_url"]} == {None, "https://maps.app.goo.gl/xyz"}


def test_moves_and_reminders_say_where_to_go(client, make, db, user_headers) -> None:
    hq = make.location(name="HQ")
    hq.address = "1 Main St"
    make.db.commit()
    service = make.service()
    resource = make.resource(services=[service], location=hq, hours=make.weekdays(time(9), time(17)))
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    moved = client.post(f"/api/bookings/{booking['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 11)}).json()

    note = next(n for n in client.get("/api/notifications", headers=user_headers).json() if n["type"] == "BOOKING_RESCHEDULED")
    assert note["body"].startswith("New time:") and note["body"].endswith("\nWhere: HQ, 1 Main St")

    start = datetime.fromisoformat(moved["start_datetime"])
    freeze_time(start - timedelta(minutes=30))
    assert notifications.queue_reminders(db, start - timedelta(minutes=30)) == 1
    reminder = db.scalars(
        select(Notification).where(Notification.type == NotificationType.BOOKING_REMINDER, Notification.channel == NotificationChannel.EMAIL)
    ).one()
    assert reminder.body.endswith("\nWhere: HQ, 1 Main St\nMap: https://www.google.com/maps/search/?api=1&query=HQ%2C+1+Main+St")


def test_settings_validation_and_update(client, admin_headers) -> None:
    r = client.put("/api/admin/settings", headers=admin_headers, json={"values": {"minimum_booking_notice": -5}})
    assert r.status_code == 422
    r = client.put("/api/admin/settings", headers=admin_headers, json={"values": {"unknown_key": 1}})
    assert r.status_code == 422
    r = client.put("/api/admin/settings", headers=admin_headers, json={"values": {"minimum_booking_notice": 90, "allow_waitlist": False}})
    assert r.json()["values"]["minimum_booking_notice"] == 90
    assert client.get("/api/config").json()["allow_waitlist"] is False


def test_waitlist_can_be_disabled(client, make, user_headers, admin_headers) -> None:
    client.put("/api/admin/settings", headers=admin_headers, json={"values": {"allow_waitlist": False}})
    service = make.service("Class", booking_type=BookingType.CAPACITY, capacity=1)
    resource = make.resource(services=[service], hours=[(0, time(18), time(19))])
    other = make.user("other@test.io")
    from tests.conftest import auth_headers

    book(client, auth_headers(client, other.email), service, resource, at(MONDAY, 18))
    r = book(client, user_headers, service, resource, at(MONDAY, 18), join_waitlist=True)
    assert r.status_code == 409 and r.json()["code"] == "CAPACITY_REACHED"


def test_dashboard_calendar_and_audit(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    book(client, user_headers, service, resource, at(MONDAY, 10))
    block(client, admin_headers, resource, at(MONDAY, 13), at(MONDAY, 14))

    dash = client.get("/api/admin/dashboard", headers=admin_headers).json()
    assert dash["upcoming_bookings"] == 1 and dash["active_resources"] == 1
    util = dash["resource_utilization"][0]
    assert util["booked_minutes"] == 60 and util["available_minutes"] > 0

    cal = client.get(
        "/api/admin/calendar",
        headers=admin_headers,
        params={"start": MONDAY.isoformat(), "end": (MONDAY + timedelta(days=6)).isoformat()},
    ).json()
    assert len(cal["bookings"]) == 1 and len(cal["blocks"]) == 1

    actions = {e["action"] for e in client.get("/api/admin/audit-logs", headers=admin_headers).json()["items"]}
    assert {"BOOKING_CREATED", "BLOCK_CREATED"} <= actions


def test_admin_user_management_guards(client, admin, admin_headers, user) -> None:
    r = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"name": "Staff", "email": "staff@test.io", "password": "password123", "role": "STAFF"},
    )
    assert r.status_code == 201
    # Only a SUPER_ADMIN may create another SUPER_ADMIN.
    r = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"name": "Boss", "email": "boss@test.io", "password": "password123", "role": "SUPER_ADMIN"},
    )
    assert r.status_code == 403
    assert client.put(f"/api/admin/users/{admin.id}", headers=admin_headers, json={"role": "USER"}).status_code == 400
    assert client.put(f"/api/admin/users/{admin.id}", headers=admin_headers, json={"status": "SUSPENDED"}).status_code == 400
    suspended = client.put(f"/api/admin/users/{user.id}", headers=admin_headers, json={"status": "SUSPENDED"})
    assert suspended.json()["status"] == "SUSPENDED"
    login = client.post("/api/auth/login", json={"email": user.email, "password": "password123"})
    assert login.status_code == 403
