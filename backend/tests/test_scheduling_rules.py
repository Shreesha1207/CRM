"""Pure scheduling logic: interval maths, weekly rules, timezones, DST."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.timeutils import day_bounds, ensure_aware
from app.services.availability import candidate_starts
from app.services.booking_engine import RecurrenceSpec, expand_recurrence
from app.services.intervals import contains, intersect, normalize, overlaps, subtract
from app.services.schedule import WeeklyRule, build_schedule, expand_weekly

UTC_TZ = ZoneInfo("UTC")


def dt(h: int, m: int = 0, day: int = 1) -> datetime:
    return datetime(2027, 3, day, h, m, tzinfo=UTC)


def test_overlap_rule_matches_spec_examples() -> None:
    existing = (dt(10), dt(11))
    assert overlaps(dt(10, 30), dt(11, 30), *existing)  # partial overlap -> conflict
    assert not overlaps(dt(11), dt(12), *existing)  # touching -> no conflict
    assert overlaps(dt(9), dt(12), *existing)  # containing -> conflict
    assert overlaps(dt(10, 15), dt(10, 45), *existing)  # contained -> conflict


def test_interval_algebra() -> None:
    assert normalize([(dt(9), dt(10)), (dt(10), dt(11)), (dt(13), dt(14))]) == [(dt(9), dt(11)), (dt(13), dt(14))]
    assert intersect([(dt(8), dt(20))], [(dt(9), dt(12)), (dt(14), dt(18))]) == [(dt(9), dt(12)), (dt(14), dt(18))]
    assert subtract([(dt(9), dt(18))], [(dt(12), dt(14))]) == [(dt(9), dt(12)), (dt(14), dt(18))]
    assert contains([(dt(9), dt(12))], dt(11), dt(12))
    assert not contains([(dt(9), dt(12)), (dt(12, 30), dt(14))], dt(11, 30), dt(12, 30))


def test_weekly_rules_with_break_and_multiple_periods() -> None:
    monday = date(2027, 3, 1)
    rules = [
        WeeklyRule(0, time(9), time(18)),
        WeeklyRule(0, time(12), time(14), is_available=False),  # lunch
        WeeklyRule(1, time(10), time(16)),
    ]
    assert expand_weekly(rules, monday, UTC_TZ) == [(dt(9), dt(12)), (dt(14), dt(18))]
    assert expand_weekly(rules, monday + timedelta(days=2), UTC_TZ) == []  # Wednesday: unavailable


def test_validity_window_of_rules() -> None:
    rule = WeeklyRule(0, time(9), time(10), valid_from=date(2027, 3, 8))
    assert expand_weekly([rule], date(2027, 3, 1), UTC_TZ) == []
    assert expand_weekly([rule], date(2027, 3, 8), UTC_TZ) != []


def test_overnight_rule_crosses_midnight() -> None:
    rule = WeeklyRule(0, time(22), time(2))
    assert expand_weekly([rule], date(2027, 3, 1), UTC_TZ) == [(dt(22), dt(2, day=2))]


def test_no_rules_means_unrestricted_and_special_hours_override() -> None:
    days = [date(2027, 3, 1)]
    assert build_schedule([], [], days, UTC_TZ) == [(dt(0), dt(0, day=2))]
    rules = [WeeklyRule(0, time(9), time(17))]
    special = [(dt(12), dt(15))]
    # Date-specific availability replaces the recurring rule for that date.
    assert build_schedule(rules, special, days, UTC_TZ) == [(dt(12), dt(15))]


def test_wall_clock_times_are_interpreted_in_location_timezone() -> None:
    kolkata = ZoneInfo("Asia/Kolkata")
    assert ensure_aware(datetime(2027, 3, 1, 10, 0), kolkata) == datetime(2027, 3, 1, 4, 30, tzinfo=UTC)
    # Aware input is respected as-is.
    aware = datetime(2027, 3, 1, 10, 0, tzinfo=UTC)
    assert ensure_aware(aware, kolkata) == aware


def test_dst_days_have_23_and_25_hours() -> None:
    ny = ZoneInfo("America/New_York")
    start, end = day_bounds(date(2027, 3, 14), ny)  # spring forward
    assert end - start == timedelta(hours=23)
    start, end = day_bounds(date(2027, 11, 7), ny)  # fall back
    assert end - start == timedelta(hours=25)


def test_slot_grid_on_dst_change_has_no_phantom_or_missing_hour() -> None:
    ny = ZoneInfo("America/New_York")
    day = date(2027, 3, 14)
    rules = [WeeklyRule(6, time(0), time(6))]  # Sunday 00:00-06:00 local
    base = build_schedule(rules, [], [day], ny)
    day_start, day_end = day_bounds(day, ny)
    starts = candidate_starts(base, timedelta(hours=1), timedelta(hours=1), day_start, day_end)
    local = [s.astimezone(ny).strftime("%H:%M") for s in starts]
    # 02:00 does not exist on this date; the window is only 5 real hours long.
    assert local == ["00:00", "01:00", "03:00", "04:00", "05:00"]


def test_weekly_recurrence_keeps_local_time_across_dst() -> None:
    ny = ZoneInfo("America/New_York")
    first = datetime(2027, 3, 8, 10, 0)  # Monday before the change
    local_starts = expand_recurrence(first, RecurrenceSpec(count=3))
    utc_hours = [ensure_aware(s, ny).hour for s in local_starts]
    assert [s.hour for s in local_starts] == [10, 10, 10]
    assert utc_hours == [15, 14, 14]  # the UTC instant shifts, the wall clock does not


def test_monthly_recurrence_clamps_to_month_end() -> None:
    from app.models.enums import RecurrenceFrequency

    starts = expand_recurrence(datetime(2027, 1, 31, 9), RecurrenceSpec(frequency=RecurrenceFrequency.MONTHLY, count=3))
    assert [s.date() for s in starts] == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)]
