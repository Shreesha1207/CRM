"""Runtime-configurable booking rules, stored in the `settings` table.

Keeping these out of code is what lets one engine serve a clinic, a gym and a
meeting-room system with no code changes.
"""

import uuid
from dataclasses import dataclass, fields
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.timeutils import is_valid_timezone
from app.models import SettingEntry

# key -> (default, description). Durations are in minutes unless noted.
SETTING_DEFINITIONS: dict[str, tuple[Any, str]] = {
    "business_name": ("Booking Platform", "Name shown in the UI and e-mails."),
    "default_timezone": ("UTC", "Timezone for resources without a location."),
    "slot_interval": (
        0,
        "Minutes between slot starts. 0 = the service's length. Starts are never closer together than "
        "a service's length, so a booking never leaves a gap too short to book.",
    ),
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
    "inactive_account_deletion_days": (
        365,
        "Admins may delete a customer account once it has been Inactive this many days. "
        "0 = only super admins delete accounts.",
    ),
    "assistant_knowledge": (
        "",
        "Facts and policies the booking assistant may use to answer customers' questions "
        "(parking, what to bring, refunds...). Leave empty if there are none.",
    ),
}

# Free text that may be empty.
OPTIONAL_TEXT = {"assistant_knowledge": 20_000}


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


class SettingsConflict(Exception):
    """Someone else saved some of these settings after the editor loaded them."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(", ".join(keys))
        self.keys = keys


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
    if key in OPTIONAL_TEXT:
        if not isinstance(value, str):
            raise SettingsValidationError(f"{key} must be text")
        if len(value) > OPTIONAL_TEXT[key]:
            raise SettingsValidationError(f"{key} must be at most {OPTIONAL_TEXT[key]} characters")
        return value.strip()
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


def _version(entry: SettingEntry | None) -> str | None:
    """Changes whenever the setting is saved; None while it has its default."""
    return entry.updated_at.isoformat() if entry is not None and entry.updated_at else None


def get_versions(db: Session) -> dict[str, str | None]:
    versions: dict[str, str | None] = dict.fromkeys(SETTING_DEFINITIONS)
    for entry in db.scalars(select(SettingEntry)):
        if entry.key in versions:
            versions[entry.key] = _version(entry)
    return versions


def update(
    db: Session,
    changes: dict[str, Any],
    actor_id: uuid.UUID | None,
    *,
    expected: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Save `changes`. With `expected` (the versions the editor loaded), a setting
    someone else saved in the meantime is not overwritten: SettingsConflict names them."""
    unknown = set(changes) - set(SETTING_DEFINITIONS)
    if unknown:
        raise SettingsValidationError(f"Unknown settings: {', '.join(sorted(unknown))}")
    values = {key: _coerce(key, raw) for key, raw in changes.items()}
    # Locked, so two admins saving at once take turns and the second sees the first's change.
    entries = {
        e.key: e
        for e in db.scalars(
            select(SettingEntry).where(SettingEntry.key.in_(list(values))).order_by(SettingEntry.key).with_for_update()
        )
    }
    if expected is not None:
        stale = sorted(k for k in values if k in expected and _version(entries.get(k)) != expected[k])
        if stale:
            raise SettingsConflict(stale)
    for key, value in values.items():
        entry = entries.get(key)
        if entry is None:
            db.add(SettingEntry(key=key, value=value, updated_by=actor_id))
        else:
            entry.value = value
            entry.updated_by = actor_id
    try:
        db.flush()
    except IntegrityError:
        # Another admin saved a setting that still had its default, at the same moment.
        db.rollback()
        raise SettingsConflict(sorted(k for k in values if k not in entries)) from None
    return get_all(db)


def describe() -> list[dict[str, Any]]:
    return [
        {"key": key, "default": default, "description": description}
        for key, (default, description) in SETTING_DEFINITIONS.items()
    ]
