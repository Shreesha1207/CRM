"""Runtime-configurable booking rules, stored in the `settings` table.

Keeping these out of code is what lets one engine serve a clinic, a gym and a
meeting-room system with no code changes.
"""

import uuid
from dataclasses import dataclass, fields
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutils import is_valid_timezone
from app.models import SettingEntry

# key -> (default, description). Durations are in minutes unless noted.
SETTING_DEFINITIONS: dict[str, tuple[Any, str]] = {
    "business_name": ("Booking Platform", "Name shown in the UI and e-mails."),
    "default_timezone": ("UTC", "Timezone for resources without a location."),
    "slot_interval": (0, "Minutes between generated slot starts. 0 = use the service duration."),
    "minimum_booking_notice": (0, "Minutes of notice required before a booking starts."),
    "maximum_advance_booking_days": (90, "How many days ahead users may book."),
    "cancellation_window": (0, "Users may cancel until this many minutes before the start."),
    "rescheduling_window": (0, "Users may reschedule until this many minutes before the start."),
    "default_buffer_before": (0, "Buffer before a booking when the service defines none."),
    "default_buffer_after": (0, "Buffer after a booking when the service defines none."),
    "allow_waitlist": (True, "Let users join a waitlist when a capacity session is full."),
    "allow_recurring_bookings": (True, "Let users create recurring bookings."),
    "max_recurring_occurrences": (52, "Upper limit of occurrences in one recurring booking."),
    "require_admin_confirmation": (False, "New user bookings start as PENDING until an admin confirms."),
    "pending_consumes_capacity": (True, "PENDING bookings hold their slot and capacity."),
    "enforce_operating_hours": (True, "Reject bookings outside operating hours."),
    "reminder_offsets": ([1440, 60], "Minutes before the start at which reminders are sent."),
}


@dataclass(frozen=True)
class BookingRules:
    business_name: str
    default_timezone: str
    slot_interval: int
    minimum_booking_notice: int
    maximum_advance_booking_days: int
    cancellation_window: int
    rescheduling_window: int
    default_buffer_before: int
    default_buffer_after: int
    allow_waitlist: bool
    allow_recurring_bookings: bool
    max_recurring_occurrences: int
    require_admin_confirmation: bool
    pending_consumes_capacity: bool
    enforce_operating_hours: bool
    reminder_offsets: list[int]


class SettingsValidationError(ValueError):
    pass


def _coerce(key: str, value: Any) -> Any:
    default = SETTING_DEFINITIONS[key][0]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{key} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SettingsValidationError(f"{key} must be a non-negative integer")
        return value
    if isinstance(default, list):
        if not isinstance(value, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in value
        ):
            raise SettingsValidationError(f"{key} must be a list of positive integers")
        return sorted(set(value), reverse=True)
    if not isinstance(value, str) or not value.strip():
        raise SettingsValidationError(f"{key} must be a non-empty string")
    if key == "default_timezone" and not is_valid_timezone(value):
        raise SettingsValidationError(f"Unknown timezone: {value}")
    return value.strip()


def get_all(db: Session) -> dict[str, Any]:
    values = {key: default for key, (default, _) in SETTING_DEFINITIONS.items()}
    for entry in db.scalars(select(SettingEntry)):
        if entry.key in values:
            values[entry.key] = entry.value
    return values


def get_rules(db: Session) -> BookingRules:
    values = get_all(db)
    return BookingRules(**{f.name: values[f.name] for f in fields(BookingRules)})


def update(db: Session, changes: dict[str, Any], actor_id: uuid.UUID | None) -> dict[str, Any]:
    unknown = set(changes) - set(SETTING_DEFINITIONS)
    if unknown:
        raise SettingsValidationError(f"Unknown settings: {', '.join(sorted(unknown))}")
    for key, raw in changes.items():
        value = _coerce(key, raw)
        entry = db.get(SettingEntry, key)
        if entry is None:
            db.add(SettingEntry(key=key, value=value, updated_by=actor_id))
        else:
            entry.value = value
            entry.updated_by = actor_id
    db.flush()
    return get_all(db)


def describe() -> list[dict[str, Any]]:
    return [
        {"key": key, "default": default, "description": description}
        for key, (default, description) in SETTING_DEFINITIONS.items()
    ]
