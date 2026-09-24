import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk
from app.models.enums import (
    BookingStatus,
    BookingType,
    ExceptionType,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
    RecordStatus,
    RecurrenceFrequency,
    ResourceType,
    Role,
    UserStatus,
)


def _enum(enum_cls: type) -> Enum:
    # Stored as VARCHAR so adding a value never needs an ALTER TYPE migration.
    return Enum(enum_cls, native_enum=False, length=32, validate_strings=True)


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.USER)
    status: Mapped[UserStatus] = mapped_column(_enum(UserStatus), default=UserStatus.ACTIVE)


class AuthSession(UUIDPk, Base):
    """Server-side record behind each issued JWT, so logout really revokes it."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class PasswordResetToken(UUIDPk, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Location(UUIDPk, Timestamps, Base):
    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[RecordStatus] = mapped_column(_enum(RecordStatus), default=RecordStatus.ACTIVE)


class Resource(UUIDPk, Timestamps, Base):
    __tablename__ = "resources"

    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[ResourceType] = mapped_column(_enum(ResourceType), default=ResourceType.CUSTOM)
    description: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int | None] = mapped_column(Integer)
    # Industry-specific attributes live here instead of in extra columns.
    attributes: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    status: Mapped[RecordStatus] = mapped_column(_enum(RecordStatus), default=RecordStatus.ACTIVE)

    location: Mapped[Location | None] = relationship(lazy="joined")
    service_links: Mapped[list["ResourceService"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )
    availability_rules: Mapped[list["ResourceAvailability"]] = relationship(
        back_populates="resource", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("capacity IS NULL OR capacity > 0", name="capacity_positive"),)


class Service(UUIDPk, Timestamps, Base):
    __tablename__ = "services"

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    capacity: Mapped[int | None] = mapped_column(Integer)
    booking_type: Mapped[BookingType] = mapped_column(_enum(BookingType), default=BookingType.INDIVIDUAL)
    buffer_before: Mapped[int | None] = mapped_column(Integer)
    buffer_after: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[RecordStatus] = mapped_column(_enum(RecordStatus), default=RecordStatus.ACTIVE)

    resource_links: Mapped[list["ResourceService"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="capacity_positive"),
        CheckConstraint("buffer_before IS NULL OR buffer_before >= 0", name="buffer_before_nonneg"),
        CheckConstraint("buffer_after IS NULL OR buffer_after >= 0", name="buffer_after_nonneg"),
    )


class ResourceService(Base):
    """Which resources provide which services, with per-resource overrides."""

    __tablename__ = "resource_services"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    custom_duration: Mapped[int | None] = mapped_column(Integer)
    custom_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    custom_buffer_before: Mapped[int | None] = mapped_column(Integer)
    custom_buffer_after: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[RecordStatus] = mapped_column(_enum(RecordStatus), default=RecordStatus.ACTIVE)

    resource: Mapped[Resource] = relationship(back_populates="service_links")
    service: Mapped[Service] = relationship(back_populates="resource_links")


class OperatingHours(UUIDPk, Base):
    """Business hours. location_id NULL = default for every location."""

    __tablename__ = "operating_hours"

    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0 = Monday ... 6 = Sunday
    start_time: Mapped[time] = mapped_column(Time)
    # end_time <= start_time means the period runs past midnight.
    end_time: Mapped[time] = mapped_column(Time)

    __table_args__ = (CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_of_week_range"),)


class ResourceAvailability(UUIDPk, Base):
    """Recurring weekly availability of one resource, in its location's timezone."""

    __tablename__ = "resource_availability"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    # False rows carve recurring gaps (e.g. a lunch break) out of the true rows.
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    resource: Mapped[Resource] = relationship(back_populates="availability_rules")

    __table_args__ = (CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_of_week_range"),)


class AvailabilityException(UUIDPk, Base):
    """Date-specific change: a block, holiday, maintenance, or special hours.

    Scope: resource_id set -> one resource; only location_id set -> every
    resource at that location; neither -> every resource.
    """

    __tablename__ = "availability_exceptions"

    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id", ondelete="CASCADE"), index=True
    )
    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[ExceptionType] = mapped_column(_enum(ExceptionType))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("end_datetime > start_datetime", name="range_valid"),
        Index("ix_availability_exceptions_range", "start_datetime", "end_datetime"),
    )


class RecurringSeries(UUIDPk, Base):
    __tablename__ = "recurring_series"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"))
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"))
    frequency: Mapped[RecurrenceFrequency | None] = mapped_column(_enum(RecurrenceFrequency))
    interval: Mapped[int] = mapped_column(Integer, default=1)
    occurrences: Mapped[int] = mapped_column(Integer)
    first_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Booking(UUIDPk, Timestamps, Base):
    __tablename__ = "bookings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id"), index=True)
    primary_resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), index=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))

    start_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[BookingStatus] = mapped_column(_enum(BookingStatus), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    conflict_reason: Mapped[str | None] = mapped_column(Text)

    # Rescheduling creates a replacement booking and links it back here.
    rescheduled_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), index=True
    )
    recurring_series_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurring_series.id", ondelete="SET NULL"), index=True
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")
    service: Mapped[Service] = relationship(lazy="joined")
    primary_resource: Mapped[Resource] = relationship(lazy="joined")
    allocations: Mapped[list["BookingResource"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("end_datetime > start_datetime", name="range_valid"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_bookings_resource_start", "primary_resource_id", "start_datetime"),
        Index("ix_bookings_start", "start_datetime"),
    )


class BookingResource(UUIDPk, Base):
    """The time each booking holds on each resource (buffers included).

    Active rows of one resource may only overlap when they share an
    allocation_key, i.e. when they are seats in the same capacity session. The
    exclusion constraint makes PostgreSQL itself refuse a double booking even
    if application-level checks were somehow bypassed.
    """

    __tablename__ = "booking_resources"

    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resources.id"), index=True)
    occupied_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occupied_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    allocation_key: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    booking: Mapped[Booking] = relationship(back_populates="allocations")

    __table_args__ = (
        CheckConstraint("occupied_end > occupied_start", name="range_valid"),
        ExcludeConstraint(
            ("resource_id", "="),
            (text("tstzrange(occupied_start, occupied_end, '[)')"), "&&"),
            ("allocation_key", "<>"),
            where=text("active"),
            using="gist",
            name="ex_booking_resources_no_overlap",
        ),
        Index("ix_booking_resources_resource_range", "resource_id", "occupied_start", "occupied_end"),
    )


class Notification(UUIDPk, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[NotificationType] = mapped_column(_enum(NotificationType))
    channel: Mapped[NotificationChannel] = mapped_column(_enum(NotificationChannel))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[NotificationStatus] = mapped_column(
        _enum(NotificationStatus), default=NotificationStatus.PENDING
    )
    # Unique so a reminder can never be recorded (and therefore sent) twice.
    dedupe_key: Mapped[str | None] = mapped_column(String(300), unique=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(UUIDPk, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SettingEntry(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
