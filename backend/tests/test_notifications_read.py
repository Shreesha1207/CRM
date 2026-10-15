from sqlalchemy import select

from app.models import Notification
from app.models.enums import NotificationChannel, NotificationType
from app.services.notifications import notify


def make_notifications(db, user, count=2, **kw):
    for i in range(count):
        notify(db, user_id=user.id, type=NotificationType.BOOKING_CREATED, title=f"n{i}", body="b", **kw)
    db.commit()


def unread(client, headers) -> int:
    return client.get("/api/notifications/unread-count", headers=headers).json()["count"]


def test_reading_one_notification_marks_only_that_one(client, db, user, user_headers) -> None:
    make_notifications(db, user, channels=(NotificationChannel.IN_APP,))
    first, second = client.get("/api/notifications", headers=user_headers).json()
    assert unread(client, user_headers) == 2

    read = client.post(f"/api/notifications/{first['id']}/read", headers=user_headers)

    assert read.status_code == 200 and read.json()["read_at"] is not None
    assert unread(client, user_headers) == 1
    listed = {n["id"]: n["read_at"] for n in client.get("/api/notifications", headers=user_headers).json()}
    assert listed[first["id"]] is not None and listed[second["id"]] is None


def test_mark_all_read_clears_the_count_and_the_list(client, db, user, user_headers) -> None:
    make_notifications(db, user, count=3)
    assert unread(client, user_headers) == 3

    assert client.post("/api/notifications/read-all", headers=user_headers).status_code == 200

    assert unread(client, user_headers) == 0
    assert all(n["read_at"] for n in client.get("/api/notifications", headers=user_headers).json())
    assert client.get("/api/notifications", headers=user_headers, params={"unread_only": True}).json() == []
    # The e-mail copies are marked too, so nothing is left behind.
    assert db.scalars(select(Notification).where(Notification.read_at.is_(None))).all() == []


def test_mark_all_read_leaves_other_peoples_notifications(client, db, make, user, user_headers) -> None:
    other = make.user("other@test.io")
    make_notifications(db, user)
    make_notifications(db, other)

    client.post("/api/notifications/read-all", headers=user_headers)

    assert db.scalars(select(Notification).where(Notification.user_id == other.id, Notification.read_at.is_(None))).all() != []


def test_cannot_read_someone_elses_notification(client, db, make, user_headers) -> None:
    other = make.user("other@test.io")
    make_notifications(db, other, count=1)
    theirs = db.scalars(select(Notification).where(Notification.user_id == other.id)).first()

    assert client.post(f"/api/notifications/{theirs.id}/read", headers=user_headers).status_code == 404
