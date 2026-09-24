from datetime import time, timedelta

from app.models import ResourceService
from app.models.enums import ResourceType
from tests.conftest import MONDAY, at


def recurring(client, headers, service, resource, *, path="/api/bookings/recurring/preview", **extra):
    return client.post(
        path,
        headers=headers,
        json={
            "service_id": str(service.id),
            "resource_id": str(resource.id),
            "start": at(MONDAY, 10),
            "frequency": "WEEKLY",
            "count": 8,
            **extra,
        },
    )


def test_recurring_occurrences_validated_independently(client, basic, admin_headers, user_headers) -> None:
    service, resource = basic
    conflict_day = MONDAY + timedelta(weeks=3)
    client.post(
        "/api/admin/exceptions",
        headers=admin_headers,
        json={"resource_id": str(resource.id), "start": at(conflict_day, 0), "end": at(conflict_day + timedelta(days=1), 0), "type": "HOLIDAY"},
    )

    preview = recurring(client, user_headers, service, resource).json()
    assert preview["available_count"] == 7 and preview["conflict_count"] == 1
    bad = [o for o in preview["occurrences"] if not o["available"]][0]
    assert bad["start"].startswith(conflict_day.isoformat()) and bad["code"] == "BLOCKED"

    # Without an explicit decision the conflicts are returned, nothing is booked.
    refused = recurring(client, user_headers, service, resource, path="/api/bookings/recurring")
    assert refused.status_code == 409 and refused.json()["code"] == "RECURRING_CONFLICTS"
    assert client.get("/api/bookings", headers=user_headers).json()["total"] == 0

    created = recurring(client, user_headers, service, resource, path="/api/bookings/recurring", skip_conflicts=True).json()
    assert len(created["created"]) == 7 and len(created["skipped"]) == 1
    assert len({b["recurring_series_id"] for b in created["created"]}) == 1


def test_recurring_can_be_disabled_for_users(client, make, basic, user_headers) -> None:
    service, resource = basic
    make.settings(allow_recurring_bookings=False)
    r = recurring(client, user_headers, service, resource)
    assert r.status_code == 422 and r.json()["code"] == "RECURRING_DISABLED"


def test_multi_resource_booking_is_atomic(client, make, user_headers) -> None:
    shoot = make.service("Photography Session", duration=120)
    photographer = make.resource("Photographer", services=[shoot], hours=make.weekdays(time(9), time(17)))
    studio = make.resource("Studio", type=ResourceType.ROOM, hours=make.weekdays(time(9), time(17)))
    camera = make.resource("Camera Kit", type=ResourceType.EQUIPMENT, hours=make.weekdays(time(9), time(17)))
    rental = make.service("Camera rental")
    make.db.add(ResourceService(resource_id=camera.id, service_id=rental.id))
    make.db.commit()

    # Someone rents the camera 11:00-12:00.
    r = client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(rental.id), "resource_id": str(camera.id), "start": at(MONDAY, 11)},
    )
    assert r.status_code == 201

    payload = {
        "service_id": str(shoot.id),
        "resource_id": str(photographer.id),
        "additional_resource_ids": [str(studio.id), str(camera.id)],
    }
    # 10:00-12:00 needs the camera, which is taken: nothing at all is reserved.
    clash = client.post("/api/bookings", headers=user_headers, json={**payload, "start": at(MONDAY, 10)})
    assert clash.status_code == 409 and "Camera Kit" in clash.json()["detail"]
    solo = client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(shoot.id), "resource_id": str(photographer.id), "start": at(MONDAY, 10)},
    )
    assert solo.status_code == 201  # photographer was not left half-reserved

    ok = client.post("/api/bookings", headers=user_headers, json={**payload, "start": at(MONDAY, 13)})
    assert ok.status_code == 201, ok.text
    assert {r["name"] for r in ok.json()["additional_resources"]} == {"Studio", "Camera Kit"}

    # The extra resources are held for the whole session too.
    camera_clash = client.post(
        "/api/bookings",
        headers=user_headers,
        json={"service_id": str(rental.id), "resource_id": str(camera.id), "start": at(MONDAY, 14)},
    )
    assert camera_clash.status_code == 409 and camera_clash.json()["code"] == "CONFLICT"
