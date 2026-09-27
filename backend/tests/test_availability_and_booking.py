from datetime import time, timedelta
from decimal import Decimal

from app.core.timeutils import freeze_time
from app.models import ResourceService
from app.models.enums import BookingType
from tests.conftest import MONDAY, NOW, at, auth_headers


def slots(client, service, resource, day=MONDAY, **params):
    r = client.get(
        "/api/availability",
        params={"service_id": str(service.id), "resource_id": str(resource.id), "date": day.isoformat(), **params},
    )
    assert r.status_code == 200, r.text
    return r.json()["resources"][0]["slots"]


def book(client, headers, service, resource, start, **extra):
    return client.post(
        "/api/bookings",
        headers=headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": start, **extra},
    )


# ---------------------------------------------------------------- slot generation


def test_slot_generation_matches_spec_example(client, make) -> None:
    service = make.service(duration=30)
    resource = make.resource(services=[service], hours=[(0, time(9), time(12))])
    got = slots(client, service, resource)
    assert [s["start_time"] for s in got] == ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]
    assert all(s["available"] for s in got)


def test_multiple_periods_and_operating_hours_intersection(client, make) -> None:
    service = make.service(duration=60)
    resource = make.resource(services=[service], hours=[(0, time(7), time(12)), (0, time(14), time(20))])
    make.operating_hours([(0, time(9), time(18))])
    got = [s["start_time"] for s in slots(client, service, resource)]
    # 07-09 and 18-20 are outside operating hours; 12-14 is a gap in availability.
    assert got == ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"]


def test_closed_day_has_no_slots(client, make) -> None:
    service = make.service()
    resource = make.resource(services=[service])  # no own schedule: follows operating hours
    make.operating_hours([(d, time(9), time(17)) for d in range(6)])  # Sunday closed
    assert slots(client, service, resource, day=MONDAY + timedelta(days=6)) == []


def test_only_slots_that_fit_the_whole_duration(client, make) -> None:
    service = make.service(duration=45)
    resource = make.resource(services=[service], hours=[(0, time(9), time(10, 30))])
    assert [s["start_time"] for s in slots(client, service, resource)] == ["09:00", "09:45"]


def test_slots_are_shown_in_location_timezone(client, make) -> None:
    kolkata = make.location("Asia/Kolkata")
    service = make.service()
    resource = make.resource(services=[service], location=kolkata, hours=[(0, time(9), time(11))])
    got = slots(client, service, resource)
    assert [s["start_time"] for s in got] == ["09:00", "10:00"]
    assert got[0]["start"].endswith("+05:30")


def test_both_booking_flows_service_first_lists_all_resources(client, make) -> None:
    service = make.service()
    a = make.resource("A", services=[service], hours=[(0, time(9), time(11))])
    make.resource("B", services=[service], hours=[(0, time(10), time(12))])
    r = client.get("/api/availability", params={"service_id": str(service.id), "date": MONDAY.isoformat()})
    body = r.json()
    assert {x["resource_name"] for x in body["resources"]} == {"A", "B"}
    ten = next(s for s in body["slots"] if s["start_time"] == "10:00")
    assert len(ten["resource_ids"]) == 2
    nine = next(s for s in body["slots"] if s["start_time"] == "09:00")
    assert nine["resource_ids"] == [str(a.id)]


# ---------------------------------------------------------------- clash detection


def test_clash_detection(client, basic, user_headers) -> None:
    service, resource = basic
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201
    clash = book(client, user_headers, service, resource, at(MONDAY, 10, 30))
    assert clash.status_code == 409
    assert clash.json()["code"] == "CONFLICT"
    # Back-to-back is fine without buffers.
    assert book(client, user_headers, service, resource, at(MONDAY, 11)).status_code == 201
    statuses = {s["start_time"]: s["status"] for s in slots(client, service, resource)}
    assert statuses["10:00"] == "CONFLICT" and statuses["12:00"] == "AVAILABLE"


def test_buffers_extend_the_occupied_time(client, make, user_headers) -> None:
    service = make.service(duration=60, buffer_before=10, buffer_after=15)
    resource = make.resource(services=[service], hours=make.weekdays(time(8), time(17)))
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201
    # 10:00-11:00 occupies 09:50-11:15, and a new booking needs 10 minutes before it.
    assert book(client, user_headers, service, resource, at(MONDAY, 11)).json()["code"] == "CONFLICT"
    assert book(client, user_headers, service, resource, at(MONDAY, 11, 20)).json()["code"] == "CONFLICT"
    assert book(client, user_headers, service, resource, at(MONDAY, 11, 25)).status_code == 201
    # 08:40 would occupy until 09:55, running into the 09:50 buffer; 08:35 just touches it.
    assert book(client, user_headers, service, resource, at(MONDAY, 8, 40)).json()["code"] == "CONFLICT"
    assert book(client, user_headers, service, resource, at(MONDAY, 8, 35)).status_code == 201


def test_rejections_for_rules(client, make, basic, user_headers) -> None:
    service, resource = basic
    make.settings(minimum_booking_notice=120, maximum_advance_booking_days=30)
    freeze_time(NOW.replace(day=1, month=3, hour=10))  # Monday 10:00
    cases = {
        at(MONDAY, 9): "PAST",
        at(MONDAY, 11): "TOO_SOON",
        at(MONDAY, 17): "OUTSIDE_AVAILABILITY",
        at(MONDAY + timedelta(days=36), 10): "TOO_FAR_AHEAD",
    }
    for start, code in cases.items():
        r = book(client, user_headers, service, resource, start)
        assert r.status_code == 409 and r.json()["code"] == code, (start, r.text)
    assert book(client, user_headers, service, resource, at(MONDAY, 12)).status_code == 201


def test_blocked_time_overrides_availability(client, basic, admin_headers, user_headers) -> None:
    service, resource = basic
    r = client.post(
        "/api/admin/exceptions",
        headers=admin_headers,
        json={"resource_id": str(resource.id), "start": at(MONDAY, 12), "end": at(MONDAY, 14), "type": "MAINTENANCE", "reason": "Deep clean"},
    )
    assert r.status_code == 200 and r.json()["applied"]
    rejected = book(client, user_headers, service, resource, at(MONDAY, 13))
    assert rejected.json()["code"] == "BLOCKED"
    assert "Deep clean" in rejected.json()["detail"]
    statuses = {s["start_time"]: s["status"] for s in slots(client, service, resource)}
    assert statuses["12:00"] == statuses["13:00"] == "BLOCKED"
    assert statuses["14:00"] == "AVAILABLE"


def test_special_hours_override_recurring_schedule(client, basic, admin_headers) -> None:
    service, resource = basic
    client.post(
        "/api/admin/exceptions",
        headers=admin_headers,
        json={"resource_id": str(resource.id), "start": at(MONDAY, 18), "end": at(MONDAY, 21), "type": "SPECIAL_HOURS"},
    )
    assert [s["start_time"] for s in slots(client, service, resource)] == ["18:00", "19:00", "20:00"]


def test_booking_across_midnight(client, make, user_headers) -> None:
    service = make.service(duration=60)
    resource = make.resource(services=[service], hours=[(0, time(22), time(2))])
    r = book(client, user_headers, service, resource, at(MONDAY, 23, 30))
    assert r.status_code == 201, r.text
    assert r.json()["end_datetime"].startswith((MONDAY + timedelta(days=1)).isoformat())
    # Notifications and e-mails give the end its own date.
    [notification] = client.get("/api/notifications", headers=user_headers).json()
    assert "Mon 01 Mar 2027, 23:30 – Tue 02 Mar 2027, 00:30 (UTC)" in notification["body"]


def test_inactive_resource_and_unoffered_service(client, make, basic, admin_headers, user_headers) -> None:
    service, resource = basic
    other = make.service("Other")
    assert book(client, user_headers, other, resource, at(MONDAY, 10)).json()["code"] == "SERVICE_NOT_OFFERED"
    payload = {"name": resource.name, "status": "INACTIVE"}
    client.put(f"/api/admin/resources/{resource.id}", headers=admin_headers, json=payload)
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).json()["code"] == "RESOURCE_INACTIVE"


def test_auto_assign_when_no_resource_chosen(client, make, user_headers) -> None:
    service = make.service()
    a = make.resource("A", services=[service], hours=[(0, time(9), time(12))])
    b = make.resource("B", services=[service], hours=[(0, time(9), time(12))])
    first = client.post("/api/bookings", headers=user_headers, json={"service_id": str(service.id), "start": at(MONDAY, 9)})
    second = client.post("/api/bookings", headers=user_headers, json={"service_id": str(service.id), "start": at(MONDAY, 9)})
    third = client.post("/api/bookings", headers=user_headers, json={"service_id": str(service.id), "start": at(MONDAY, 9)})
    assert {first.json()["resource"]["id"], second.json()["resource"]["id"]} == {str(a.id), str(b.id)}
    assert third.status_code == 409 and third.json()["code"] == "NO_RESOURCE_AVAILABLE"


# ---------------------------------------------------------------- capacity & waitlist


def test_capacity_and_waitlist_promotion(client, make) -> None:
    from tests.conftest import auth_headers

    service = make.service("Yoga", booking_type=BookingType.CAPACITY, capacity=2)
    resource = make.resource("Studio", services=[service], hours=[(0, time(18), time(19))])
    users = [make.user(f"u{i}@test.io") for i in range(4)]
    headers = [auth_headers(client, u.email) for u in users]

    first = book(client, headers[0], service, resource, at(MONDAY, 18))
    assert first.status_code == 201
    assert book(client, headers[1], service, resource, at(MONDAY, 18)).status_code == 201
    full = book(client, headers[2], service, resource, at(MONDAY, 18))
    assert full.status_code == 409 and full.json()["code"] == "CAPACITY_REACHED"

    waitlisted = book(client, headers[2], service, resource, at(MONDAY, 18), join_waitlist=True).json()
    assert waitlisted["status"] == "WAITLISTED" and waitlisted["waitlist_position"] == 1
    second_wait = book(client, headers[3], service, resource, at(MONDAY, 18), join_waitlist=True).json()
    assert second_wait["waitlist_position"] == 2

    slot = slots(client, service, resource)[0]
    assert slot["remaining"] == 0 and slot["status"] == "CAPACITY_REACHED"

    client.post(f"/api/bookings/{first.json()['id']}/cancel", headers=headers[0], json={})
    promoted = client.get(f"/api/bookings/{waitlisted['id']}", headers=headers[2]).json()
    assert promoted["status"] == "CONFIRMED"
    still_waiting = client.get(f"/api/bookings/{second_wait['id']}", headers=headers[3]).json()
    assert still_waiting["status"] == "WAITLISTED" and still_waiting["waitlist_position"] == 1
    assert slots(client, service, resource)[0]["remaining"] == 0


def test_same_user_cannot_take_two_seats_in_one_session(client, make, user_headers) -> None:
    service = make.service("Class", booking_type=BookingType.CAPACITY, capacity=5)
    resource = make.resource(services=[service], hours=[(0, time(18), time(19))])
    assert book(client, user_headers, service, resource, at(MONDAY, 18)).status_code == 201
    assert book(client, user_headers, service, resource, at(MONDAY, 18)).json()["code"] == "ALREADY_BOOKED"


def test_quantity_counts_against_capacity(client, make, user_headers) -> None:
    service = make.service("Class", booking_type=BookingType.CAPACITY, capacity=5)
    resource = make.resource(services=[service], hours=[(0, time(18), time(19))])
    assert book(client, user_headers, service, resource, at(MONDAY, 18), quantity=6).json()["code"] == "QUANTITY_EXCEEDS_CAPACITY"
    assert book(client, user_headers, service, resource, at(MONDAY, 18), quantity=4).status_code == 201
    assert slots(client, service, resource, quantity=2)[0]["status"] == "CAPACITY_REACHED"


def test_capacity_session_blocks_exclusive_booking_on_same_resource(client, make, user_headers) -> None:
    group = make.service("Group", booking_type=BookingType.CAPACITY, capacity=10)
    private = make.service("Private")
    resource = make.resource(services=[group, private], hours=make.weekdays(time(9), time(17)))
    assert book(client, user_headers, group, resource, at(MONDAY, 10)).status_code == 201
    assert book(client, user_headers, private, resource, at(MONDAY, 10, 30)).json()["code"] == "CONFLICT"


# ---------------------------------------------------------------- cancel & reschedule


def test_cancelled_bookings_are_kept_and_free_the_slot(client, basic, user_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    r = client.post(f"/api/bookings/{booking['id']}/cancel", headers=user_headers, json={"reason": "Sick"})
    assert r.json()["status"] == "CANCELLED" and r.json()["cancellation_reason"] == "Sick"
    history = client.get("/api/bookings", headers=user_headers, params={"scope": "cancelled"}).json()
    assert history["total"] == 1
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201


def test_cancellation_window(client, make, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    make.settings(cancellation_window=24 * 60)
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    assert booking["can_cancel"] is True
    freeze_time(NOW.replace(day=28, month=2, hour=12))  # Sunday noon: < 24h before
    r = client.post(f"/api/bookings/{booking['id']}/cancel", headers=user_headers, json={})
    assert r.status_code == 409 and r.json()["code"] == "CANCELLATION_WINDOW_PASSED"
    # Admins may override the policy; the override is audited.
    assert client.post(f"/api/admin/bookings/{booking['id']}/cancel", headers=admin_headers, json={}).json()["status"] == "CANCELLED"
    logs = client.get("/api/admin/audit-logs", headers=admin_headers, params={"action": "ADMIN_OVERRIDE"}).json()
    assert logs["total"] == 1


def test_reschedule_creates_linked_booking_and_frees_old_slot(client, basic, user_headers) -> None:
    service, resource = basic
    original = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    r = client.post(f"/api/bookings/{original['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 14)})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["rescheduled_from_id"] == original["id"] and new["status"] == "CONFIRMED"
    old = client.get(f"/api/bookings/{original['id']}", headers=user_headers).json()
    assert old["status"] == "RESCHEDULED" and old["rescheduled_to_id"] == new["id"]
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).status_code == 201


def test_reschedule_can_overlap_its_own_old_time(client, basic, user_headers) -> None:
    service, resource = basic
    original = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    r = client.post(f"/api/bookings/{original['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 10, 30)})
    assert r.status_code == 200, r.text


def test_availability_can_leave_out_the_booking_being_moved(client, make, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    make.settings(slot_interval=30)
    original = book(client, user_headers, service, resource, at(MONDAY, 10)).json()

    def status_at_1030(headers=None, **params):
        r = client.get(
            "/api/availability",
            headers=headers,
            params={"service_id": str(service.id), "resource_id": str(resource.id), "date": MONDAY.isoformat(), **params},
        )
        if r.status_code != 200:
            return r.status_code
        return next(s["status"] for s in r.json()["resources"][0]["slots"] if s["start_time"] == "10:30")

    assert status_at_1030(user_headers) == "CONFLICT"
    # The reschedule dialog shows what the move itself would accept.
    assert status_at_1030(user_headers, exclude_booking_id=original["id"]) == "AVAILABLE"
    assert status_at_1030(admin_headers, exclude_booking_id=original["id"]) == "AVAILABLE"
    # Only someone who can see the booking may leave it out.
    assert status_at_1030(exclude_booking_id=original["id"]) == 401
    make.user("other@test.io")
    assert status_at_1030(auth_headers(client, "other@test.io"), exclude_booking_id=original["id"]) == 404


def test_failed_reschedule_leaves_original_intact(client, basic, user_headers) -> None:
    service, resource = basic
    original = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    book(client, user_headers, service, resource, at(MONDAY, 14))
    r = client.post(f"/api/bookings/{original['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 14, 30)})
    assert r.status_code == 409
    still = client.get(f"/api/bookings/{original['id']}", headers=user_headers).json()
    assert still["status"] == "CONFIRMED" and still["rescheduled_to_id"] is None
    # ...and it still holds its slot.
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).json()["code"] == "CONFLICT"


def test_pending_bookings_when_admin_confirmation_required(client, make, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    make.settings(require_admin_confirmation=True)
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    assert booking["status"] == "PENDING"
    # Pending bookings hold their slot by default.
    assert book(client, user_headers, service, resource, at(MONDAY, 10)).json()["code"] == "CONFLICT"
    confirmed = client.post(f"/api/admin/bookings/{booking['id']}/confirm", headers=admin_headers).json()
    assert confirmed["status"] == "CONFIRMED"


def test_pending_that_does_not_consume_capacity_is_rechecked_on_confirm(client, make, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    make.settings(require_admin_confirmation=True, pending_consumes_capacity=False)
    a = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    b = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    assert client.post(f"/api/admin/bookings/{a['id']}/confirm", headers=admin_headers).status_code == 200
    second = client.post(f"/api/admin/bookings/{b['id']}/confirm", headers=admin_headers)
    assert second.status_code == 409 and second.json()["code"] == "CONFLICT"


def test_complete_and_no_show_only_after_start(client, basic, user_headers, admin_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource, at(MONDAY, 10)).json()
    assert client.post(f"/api/admin/bookings/{booking['id']}/complete", headers=admin_headers).json()["code"] == "NOT_STARTED"
    freeze_time(NOW.replace(day=1, month=3, hour=11))
    assert client.post(f"/api/admin/bookings/{booking['id']}/complete", headers=admin_headers).json()["status"] == "COMPLETED"
    again = client.post(f"/api/admin/bookings/{booking['id']}/no-show", headers=admin_headers)
    assert again.status_code == 409 and again.json()["code"] == "INVALID_STATUS_TRANSITION"


def test_admin_override_books_outside_hours_but_never_double_books(client, basic, user, admin_headers, user_headers) -> None:
    service, resource = basic
    payload = {"service_id": str(service.id), "resource_id": str(resource.id), "user_id": str(user.id)}
    late = client.post("/api/admin/bookings", headers=admin_headers, json={**payload, "start": at(MONDAY, 19), "override_rules": True})
    assert late.status_code == 201, late.text
    book(client, user_headers, service, resource, at(MONDAY, 10))
    clash = client.post("/api/admin/bookings", headers=admin_headers, json={**payload, "start": at(MONDAY, 10), "override_rules": True})
    assert clash.status_code == 409 and clash.json()["code"] == "CONFLICT"


def test_alternatives_suggested_for_taken_slot(client, make, user_headers) -> None:
    service = make.service()
    a = make.resource("A", services=[service], hours=[(0, time(9), time(12))])
    make.resource("B", services=[service], hours=[(0, time(9), time(12))])
    book(client, user_headers, service, a, at(MONDAY, 10))
    r = client.get(
        "/api/availability/alternatives",
        params={"service_id": str(service.id), "resource_id": str(a.id), "start": at(MONDAY, 10)},
    )
    options = r.json()
    assert options[0]["resource_name"] == "B" and options[0]["start"].startswith(at(MONDAY, 10))
    assert any(o["same_resource"] for o in options)


# ---------------------------------------------------------------- flexible length, hourly price


def test_flexible_length_is_booked_in_steps_and_priced_per_hour(client, make, user_headers) -> None:
    service = make.service(duration=60, price=Decimal("20"), max_duration_minutes=180)
    resource = make.resource(services=[service], hours=make.weekdays(time(9), time(17)))

    # Only starts that leave room for the whole chosen length are offered.
    assert [s["start_time"] for s in slots(client, service, resource, duration_minutes=120)][-1] == "15:00"
    assert [s["start_time"] for s in slots(client, service, resource, duration_minutes=180)][-1] == "14:00"

    r = book(client, user_headers, service, resource, at(MONDAY, 10), duration_minutes=120)
    assert r.status_code == 201, r.text
    booking = r.json()
    assert booking["end_datetime"].startswith(f"{MONDAY.isoformat()}T12:00")
    assert booking["price"] == "40.00"  # 20.00 per hour x 2 hours
    assert book(client, user_headers, service, resource, at(MONDAY, 11)).json()["code"] == "CONFLICT"
    assert book(client, user_headers, service, resource, at(MONDAY, 12)).status_code == 201

    # Lengths come in steps of the duration, up to the maximum.
    for minutes in (90, 240):
        r = book(client, user_headers, service, resource, at(MONDAY, 14), duration_minutes=minutes)
        assert r.status_code == 422 and r.json()["code"] == "INVALID_DURATION"


def test_fixed_length_services_only_take_their_own_length(client, basic, user_headers) -> None:
    service, resource = basic
    r = book(client, user_headers, service, resource, at(MONDAY, 10), duration_minutes=120)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_DURATION"
    assert book(client, user_headers, service, resource, at(MONDAY, 10), duration_minutes=60).status_code == 201


def test_price_is_an_hourly_rate(client, make, user_headers) -> None:
    half_hour = make.service("Consultation", duration=30, price=Decimal("50"))
    group = make.service("Class", duration=60, price=Decimal("12"), booking_type=BookingType.CAPACITY, capacity=10)
    a = make.resource("A", services=[half_hour], hours=make.weekdays(time(9), time(17)))
    b = make.resource("B", services=[group], hours=make.weekdays(time(9), time(17)))
    assert book(client, user_headers, half_hour, a, at(MONDAY, 9)).json()["price"] == "25.00"
    assert book(client, user_headers, group, b, at(MONDAY, 9), quantity=3).json()["price"] == "36.00"


def test_moving_a_flexible_booking_keeps_its_length(client, make, user_headers) -> None:
    service = make.service(duration=60, price=Decimal("20"), max_duration_minutes=180)
    resource = make.resource(services=[service], hours=make.weekdays(time(9), time(17)))
    original = book(client, user_headers, service, resource, at(MONDAY, 10), duration_minutes=120).json()

    # The reschedule picker offers times for the booking's own two hours.
    moving = client.get(
        "/api/availability",
        headers=user_headers,
        params={"service_id": str(service.id), "resource_id": str(resource.id), "date": MONDAY.isoformat(), "exclude_booking_id": original["id"]},
    ).json()["resources"][0]["slots"]
    assert moving[-1]["start_time"] == "15:00"

    r = client.post(f"/api/bookings/{original['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 13)})
    assert r.status_code == 200, r.text
    assert r.json()["end_datetime"].startswith(f"{MONDAY.isoformat()}T15:00")
    assert r.json()["price"] == "40.00"


def test_recurring_series_uses_the_chosen_length(client, make, user_headers) -> None:
    service = make.service(duration=60, price=Decimal("20"), max_duration_minutes=180)
    resource = make.resource(services=[service], hours=make.weekdays(time(9), time(17)))
    r = client.post(
        "/api/bookings/recurring/preview",
        headers=user_headers,
        json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 15), "count": 2, "duration_minutes": 180},
    )
    assert r.status_code == 200, r.text
    # 15:00 + 3 hours runs past closing at 17:00.
    assert [o["code"] for o in r.json()["occurrences"]] == ["OUTSIDE_AVAILABILITY"] * 2


def test_time_first_with_a_chosen_length_skips_resources_that_cannot_do_it(client, make, db, user_headers) -> None:
    service = make.service(duration=60, price=Decimal("20"), max_duration_minutes=120)
    a = make.resource("A", services=[service], hours=make.weekdays(time(9), time(17)))
    make.resource("B", services=[service], hours=make.weekdays(time(9), time(17)))
    # A offers the service in 45-minute steps (45 or 90 minutes), so never for two hours.
    db.get(ResourceService, (a.id, service.id)).custom_duration = 45
    db.commit()

    r = client.get("/api/availability", params={"service_id": str(service.id), "date": MONDAY.isoformat(), "duration_minutes": 120})
    assert [x["resource_name"] for x in r.json()["resources"]] == ["B"]
    r = client.get(
        "/api/availability",
        params={"service_id": str(service.id), "resource_id": str(a.id), "date": MONDAY.isoformat(), "duration_minutes": 120},
    )
    assert r.status_code == 422 and r.json()["code"] == "INVALID_DURATION"

    # "Any available" for two hours skips A and books B.
    r = client.post(
        "/api/bookings", headers=user_headers, json={"service_id": str(service.id), "start": at(MONDAY, 10), "duration_minutes": 120}
    )
    assert r.status_code == 201, r.text
    assert r.json()["resource"]["name"] == "B" and r.json()["price"] == "40.00"


def test_alternatives_keep_the_chosen_length(client, make, user_headers) -> None:
    service = make.service(duration=60, price=Decimal("20"), max_duration_minutes=120)
    a = make.resource("A", services=[service], hours=[(0, time(9), time(13))])
    make.resource("B", services=[service], hours=[(0, time(9), time(11))])
    book(client, user_headers, service, a, at(MONDAY, 10), duration_minutes=120)

    r = client.get(
        "/api/availability/alternatives",
        params={"service_id": str(service.id), "resource_id": str(a.id), "start": at(MONDAY, 10), "duration_minutes": 120},
    )
    options = [(o["resource_name"], o["start"][11:16], o["end"][11:16]) for o in r.json()]
    # Only two-hour options: B closes at 11:00, so B at 10:00 does not fit, 09:00 does.
    assert options == [("B", "09:00", "11:00")]


def test_admin_books_a_chosen_length(client, make, user, admin_headers) -> None:
    service = make.service(duration=60, price=Decimal("20"), max_duration_minutes=180)
    resource = make.resource(services=[service], hours=make.weekdays(time(9), time(17)))
    r = client.post(
        "/api/admin/bookings",
        headers=admin_headers,
        json={
            "user_id": str(user.id),
            "service_id": str(service.id),
            "resource_id": str(resource.id),
            "start": at(MONDAY, 9),
            "duration_minutes": 180,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["end_datetime"].startswith(f"{MONDAY.isoformat()}T12:00") and r.json()["price"] == "60.00"


def test_service_length_settings_are_validated(client, admin_headers) -> None:
    def create(**extra):
        return client.post("/api/admin/services", headers=admin_headers, json={"name": "S", "duration_minutes": 60, "price": "10", **extra})

    assert create(max_duration_minutes=30).status_code == 422
    assert create(max_duration_minutes=120, booking_type="CAPACITY", capacity=5).status_code == 422
    ok = create(max_duration_minutes=240)
    assert ok.status_code == 201 and ok.json()["max_duration_minutes"] == 240
