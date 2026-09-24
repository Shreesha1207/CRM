"""Admin dashboard metrics."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutils import day_bounds, get_zone
from app.models import Booking, BookingResource, Resource, User
from app.models.enums import BookingStatus, RecordStatus
from app.services import settings_service
from app.services.availability import build_snapshot
from app.services.intervals import intersect, normalize

S = BookingStatus


def _minutes(intervals: list[tuple[datetime, datetime]]) -> float:
    return sum((e - s).total_seconds() for s, e in intervals) / 60


def resource_utilization(db: Session, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Booked time / bookable time per active resource over [start, end)."""
    rules = settings_service.get_rules(db)
    out = []
    for resource in db.scalars(select(Resource).where(Resource.status == RecordStatus.ACTIVE).order_by(Resource.name)).unique():
        snapshot = build_snapshot(db, resource, start, end, rules, include_occupancy=False)
        free = intersect(snapshot.free, [(start, end)])
        booked_rows = db.execute(
            select(Booking.start_datetime, Booking.end_datetime)
            .join(BookingResource, BookingResource.booking_id == Booking.id)
            .where(
                BookingResource.resource_id == resource.id,
                BookingResource.active.is_(True),
                Booking.start_datetime < end,
                Booking.end_datetime > start,
            )
        ).all()
        # Union first: seats of one capacity session count once.
        booked = intersect(normalize([(s, e) for s, e in booked_rows]), [(start, end)])
        available_minutes = _minutes(free)
        booked_minutes = _minutes(booked)
        out.append(
            {
                "resource_id": str(resource.id),
                "resource_name": resource.name,
                "available_minutes": round(available_minutes),
                "booked_minutes": round(booked_minutes),
                "utilization": round(booked_minutes / available_minutes, 4) if available_minutes else 0.0,
            }
        )
    return out


def dashboard(db: Session, now: datetime) -> dict[str, Any]:
    rules = settings_service.get_rules(db)
    tz = get_zone(rules.default_timezone)
    today = now.astimezone(tz).date()
    today_start, today_end = day_bounds(today, tz)

    def count(*where) -> int:
        return db.scalar(select(func.count()).select_from(Booking).where(*where)) or 0

    live = [S.PENDING, S.CONFIRMED, S.CONFLICTED, S.COMPLETED, S.NO_SHOW]
    week_end = today_start + timedelta(days=7)
    return {
        "timezone": tz.key,
        "total_users": db.scalar(select(func.count()).select_from(User)) or 0,
        "active_resources": db.scalar(
            select(func.count()).select_from(Resource).where(Resource.status == RecordStatus.ACTIVE)
        )
        or 0,
        "todays_bookings": count(
            Booking.start_datetime >= today_start, Booking.start_datetime < today_end, Booking.status.in_(live)
        ),
        "upcoming_bookings": count(Booking.start_datetime >= now, Booking.status.in_([S.PENDING, S.CONFIRMED])),
        "completed_bookings": count(Booking.status == S.COMPLETED),
        "cancelled_bookings": count(Booking.status == S.CANCELLED),
        "pending_bookings": count(Booking.status == S.PENDING),
        "waitlisted_bookings": count(Booking.status == S.WAITLISTED),
        "conflicts": count(Booking.status == S.CONFLICTED),
        "utilization_window": {"start": today_start.isoformat(), "end": week_end.isoformat()},
        "resource_utilization": resource_utilization(db, today_start, week_end),
    }
