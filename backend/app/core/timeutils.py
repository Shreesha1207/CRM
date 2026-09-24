"""Timezone helpers.

Absolute instants are always stored and compared in UTC. Wall-clock values
(weekly rules, a date the user picked) are interpreted in the timezone of the
resource's location, never in the server's OS timezone.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

_frozen_now: datetime | None = None


def utcnow() -> datetime:
    """Current time in UTC. The single seam tests use to freeze time."""
    return _frozen_now if _frozen_now is not None else datetime.now(UTC)


def freeze_time(value: datetime | None) -> None:
    global _frozen_now
    _frozen_now = value.astimezone(UTC) if value is not None else None


def get_zone(name: str | None, fallback: str = "UTC") -> ZoneInfo:
    try:
        return ZoneInfo(name or fallback)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(fallback)


def is_valid_timezone(name: str) -> bool:
    return name in available_timezones()


def localize(local: datetime, tz: ZoneInfo) -> datetime:
    """Attach ``tz`` to a naive wall-clock datetime and return it in UTC.

    Ambiguous times (the repeated hour when clocks go back) resolve to the
    first occurrence; non-existent times (the skipped hour when clocks go
    forward) resolve to the equivalent instant after the jump.
    """
    return local.replace(tzinfo=tz, fold=0).astimezone(UTC)


def ensure_aware(value: datetime, tz: ZoneInfo) -> datetime:
    """Naive input is wall-clock time in ``tz``; aware input is kept as-is."""
    if value.tzinfo is None:
        return localize(value, tz)
    return value.astimezone(UTC)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC instants of local midnight at the start and end of ``day``.

    The span is 23 or 25 hours on daylight-saving transition days.
    """
    start = localize(datetime.combine(day, time.min), tz)
    end = localize(datetime.combine(day + timedelta(days=1), time.min), tz)
    return start, end


def local_date(value: datetime, tz: ZoneInfo) -> date:
    return value.astimezone(tz).date()


def date_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]
