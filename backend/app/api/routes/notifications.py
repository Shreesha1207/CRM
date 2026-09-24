import uuid

from fastapi import APIRouter, Query
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser
from app.core.errors import NotFound
from app.core.timeutils import utcnow
from app.models import Notification
from app.models.enums import NotificationChannel
from app.schemas import NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    user: CurrentUser, db: DB, unread_only: bool = False, limit: int = Query(default=30, ge=1, le=100)
) -> list[Notification]:
    stmt = select(Notification).where(
        Notification.user_id == user.id, Notification.channel == NotificationChannel.IN_APP
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return list(db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit)))


@router.get("/unread-count")
def unread_count(user: CurrentUser, db: DB) -> dict:
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.channel == NotificationChannel.IN_APP,
            Notification.read_at.is_(None),
        )
    )
    return {"count": count or 0}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DB) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise NotFound("Notification")
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.commit()
    return notification


@router.post("/read-all")
def mark_all_read(user: CurrentUser, db: DB) -> dict:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    db.commit()
    return {"ok": True}
