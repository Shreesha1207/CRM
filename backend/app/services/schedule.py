"""Expansion of weekly rules and date-specific overrides into concrete intervals.

Pure functions: no database access, so the rules are easy to unit test.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.timeutils import date_range, day_bounds, localize
from app.services.intervals import Interval, normalize, overlaps, subtract


@dataclass(frozen=True)
class WeeklyRule:
    day_of_week: int  # 0 = Monday
    start_time: time
    end_time: time
    valid_from: date | None = None
    valid_until: date | None = None
    is_available: bool = True

    def applies_on(self, day: date) -> bool:
        if day.weekday() != self.day_of_week:
            return False
        if self.valid_from and day < self.valid_from:
            return False
        if self.valid_until and day > self.valid_until:
            return False
        return True

    def interval_on(self, day: date, tz: ZoneInfo) -> Interval:
        start = localize(datetime.combine(day, self.start_time), tz)
        # An end at or before the start means the period crosses midnight,
        # e.g. 22:00-02:00, or 00:00-00:00 for a full day.
        end_day = day + timedelta(days=1) if self.end_time <= self.start_time else day
        end = localize(datetime.combine(end_day, self.end_time), tz)
        return start, end


def expand_weekly(rules: Iterable[WeeklyRule], day: date, tz: ZoneInfo) -> list[Interval]:
    """Intervals produced by the rules that start on ``day``.

    Available rules are unioned, then unavailable rules (breaks) cut out of them.
    """
    positive: list[Interval] = []
    negative: list[Interval] = []
    for rule in rules:
        if rule.applies_on(day):
            (positive if rule.is_available else negative).append(rule.interval_on(day, tz))
    return subtract(positive, negative)


def build_schedule(
    rules: Sequence[WeeklyRule],
    special_hours: Sequence[Interval],
    days: Iterable[date],
    tz: ZoneInfo,
) -> list[Interval]:
    """Concrete schedule for ``days``.

    * No weekly rules at all means the scope is unrestricted (open all day).
    * Special hours touching a local date replace that date's weekly rules
      (date-specific availability overrides recurring availability).
    """
    out: list[Interval] = []
    for day in days:
        day_start, day_end = day_bounds(day, tz)
        todays_special = [
            (max(s, day_start), min(e, day_end))
            for s, e in special_hours
            if overlaps(s, e, day_start, day_end)
        ]
        if todays_special:
            out.extend(todays_special)
        elif rules:
            out.extend(expand_weekly(rules, day, tz))
        else:
            out.append((day_start, day_end))
    return normalize(out)


def schedule_days_for_range(start: datetime, end: datetime, tz: ZoneInfo) -> list[date]:
    """Local dates whose rules may produce time inside [start, end).

    Includes the previous day so overnight rules spill over correctly.
    """
    first = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    return date_range(first, last)
