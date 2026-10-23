"""Request / response models. Pydantic validates every input at the boundary."""

import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.maps import google_maps_url
from app.core.timeutils import is_valid_timezone
from app.models.enums import (
    BookingStatus,
    BookingType,
    ExceptionType,
    NotificationChannel,
    NotificationType,
    PaymentStatus,
    RecordStatus,
    RecurrenceFrequency,
    ResourceType,
    Role,
    UserStatus,
)

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


# ---------------------------------------------------------------- auth


def _check_phone(value: str | None) -> str | None:
    """A phone number: 7 to 15 digits (the international maximum), optionally after a +
    and a country code. Spaces, dashes, dots and brackets are allowed while typing and
    dropped, so "+91 98765-43210" is stored as "+919876543210"."""
    value = (value or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"\+?[\d\s().-]+", value):
        raise ValueError("Use digits only, with an optional + and country code (e.g. +91 98765 43210)")
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        raise ValueError("A phone number has 7 to 15 digits")
    return ("+" if value.startswith("+") else "") + digits


Phone = Annotated[str | None, Field(max_length=50), AfterValidator(_check_phone)]


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: Phone = None
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class GoogleAuthIn(BaseModel):
    credential: str


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: Phone = None


class UserOut(ORM):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None
    role: Role
    status: UserStatus
    created_at: datetime


class MeOut(UserOut):
    permissions: list[str]


class AdminUserOut(UserOut):
    # When an admin may delete this (inactive customer) account; None = only a super admin.
    deletable_from: datetime | None = None
    # Whether the person listing the users may delete this one now.
    can_delete: bool = False


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    csrf_token: str
    user: MeOut


class UserCreateIn(BaseModel):
    """No password: the person is e-mailed a link to choose their own."""

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: Phone = None
    role: Role = Role.USER


class UserUpdateIn(BaseModel):
    # No password: an admin can only send a link for the person to choose one.
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: Phone = None
    role: Role | None = None
    status: UserStatus | None = None


# ---------------------------------------------------------------- locations


def _check_tz(value: str) -> str:
    if not is_valid_timezone(value):
        raise ValueError(f"Unknown timezone: {value}")
    return value


def _check_map_url(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    # Only web links: the value ends up in an href.
    if not value.lower().startswith(("https://", "http://")):
        raise ValueError("Enter a web link starting with https://")
    return value


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    map_url: str | None = Field(default=None, max_length=2000)
    timezone: str = "UTC"
    status: RecordStatus = RecordStatus.ACTIVE

    _tz = field_validator("timezone")(_check_tz)
    _map_url = field_validator("map_url")(_check_map_url)


class LocationOut(ORM):
    id: uuid.UUID
    name: str
    address: str | None
    map_url: str | None
    timezone: str
    status: RecordStatus

    @computed_field
    @property
    def google_maps_url(self) -> str | None:
        return google_maps_url(self.name, self.address, self.map_url)


class LocationRef(BaseModel):
    """A location as shown on a booking or an offering: enough to get there."""

    id: uuid.UUID
    name: str
    address: str | None
    google_maps_url: str | None


# ---------------------------------------------------------------- resources & services


class ResourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    type: ResourceType = ResourceType.CUSTOM
    capacity: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    location_id: uuid.UUID | None = None
    status: RecordStatus = RecordStatus.ACTIVE

    @field_validator("metadata")
    @classmethod
    def _attributes_have_values(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        """Custom attributes need a name and a value: no blank entries."""
        clean: dict[str, Any] = {}
        for key, value in metadata.items():
            name = key.strip()
            if not name:
                raise ValueError("Every custom attribute needs a name")
            if name in clean:
                raise ValueError(f"Custom attribute '{name}' is listed twice")
            if value is None or value in ("", [], {}) or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"Custom attribute '{name}' needs a value")
            clean[name] = value.strip() if isinstance(value, str) else value
        return clean


class ResourceOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    type: ResourceType
    capacity: int | None
    metadata: dict[str, Any]
    location_id: uuid.UUID | None
    location: LocationOut | None
    timezone: str
    status: RecordStatus
    created_at: datetime
    updated_at: datetime


class AvailabilityRuleIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    valid_from: date | None = None
    valid_until: date | None = None
    is_available: bool = True

    @model_validator(mode="after")
    def _valid_range(self) -> "AvailabilityRuleIn":
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on or after valid_from")
        return self


class AvailabilityRuleOut(ORM):
    id: uuid.UUID
    day_of_week: int
    start_time: time
    end_time: time
    valid_from: date | None
    valid_until: date | None
    is_available: bool


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    duration_minutes: int = Field(gt=0, le=24 * 60)
    # Longest booking a customer may choose, in multiples of duration_minutes.
    max_duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)
    price: Decimal | None = Field(default=None, ge=0)  # hourly rate
    capacity: int | None = Field(default=None, gt=0)
    booking_type: BookingType = BookingType.INDIVIDUAL
    buffer_before: int | None = Field(default=None, ge=0, le=24 * 60)
    buffer_after: int | None = Field(default=None, ge=0, le=24 * 60)
    status: RecordStatus = RecordStatus.ACTIVE

    @model_validator(mode="after")
    def check_length(self) -> "ServiceIn":
        if self.max_duration_minutes is not None:
            if self.booking_type != BookingType.INDIVIDUAL:
                raise ValueError("A flexible length is only possible for individual services")
            if self.max_duration_minutes < self.duration_minutes:
                raise ValueError("The longest booking cannot be shorter than the duration")
        return self


class ServiceOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    max_duration_minutes: int | None
    price: Decimal | None
    capacity: int | None
    booking_type: BookingType
    buffer_before: int | None
    buffer_after: int | None
    status: RecordStatus
    created_at: datetime
    updated_at: datetime


class ResourceServiceIn(BaseModel):
    service_id: uuid.UUID
    custom_duration: int | None = Field(default=None, gt=0, le=24 * 60)
    custom_price: Decimal | None = Field(default=None, ge=0)
    custom_buffer_before: int | None = Field(default=None, ge=0)
    custom_buffer_after: int | None = Field(default=None, ge=0)
    status: RecordStatus = RecordStatus.ACTIVE


class OfferingOut(BaseModel):
    """A service as provided by one resource, with overrides applied."""

    resource_id: uuid.UUID
    resource_name: str
    resource_type: ResourceType
    service_id: uuid.UUID
    service_name: str
    booking_type: BookingType
    duration_minutes: int
    # Longest bookable length on this resource (= duration_minutes when fixed).
    max_duration_minutes: int
    price: Decimal | None  # hourly rate
    custom_duration: int | None
    custom_price: Decimal | None
    custom_buffer_before: int | None
    custom_buffer_after: int | None
    location_name: str | None
    location: LocationRef | None
    status: RecordStatus


class ResourceDetailOut(ResourceOut):
    services: list[OfferingOut]
    availability: list[AvailabilityRuleOut]


class ServiceDetailOut(ServiceOut):
    resources: list[OfferingOut]


class LinkRef(BaseModel):
    """The other side of a resource-service link, as listed in the admin console."""

    id: uuid.UUID
    name: str
    status: RecordStatus


class AdminResourceOut(ResourceOut):
    services: list[LinkRef]


class AdminServiceOut(ServiceOut):
    resources: list[LinkRef]


# ---------------------------------------------------------------- schedules


class OperatingHoursIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    start_time: time
    end_time: time


class OperatingHoursOut(ORM):
    id: uuid.UUID
    location_id: uuid.UUID | None
    day_of_week: int
    start_time: time
    end_time: time


class ExceptionIn(BaseModel):
    resource_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    # Naive values are wall-clock time at the resource / location.
    start: datetime
    end: datetime
    type: ExceptionType = ExceptionType.BLOCKED
    reason: str | None = Field(default=None, max_length=1000)


class ExceptionOut(ORM):
    id: uuid.UUID
    resource_id: uuid.UUID | None
    location_id: uuid.UUID | None
    start_datetime: datetime
    end_datetime: datetime
    type: ExceptionType
    reason: str | None
    created_at: datetime


class AffectedBookingOut(BaseModel):
    booking_id: uuid.UUID
    code: str
    message: str
    booking: dict[str, Any]


class ScheduleChangeOut(BaseModel):
    applied: bool
    affected_count: int
    affected_bookings: list[AffectedBookingOut]
    result: Any = None


ConflictAction = Literal["keep", "mark_conflicted", "cancel"]


# ---------------------------------------------------------------- availability


class SlotOut(BaseModel):
    start: datetime
    end: datetime
    start_time: str
    end_time: str
    available: bool
    status: str
    message: str
    remaining: int | None = None
    capacity: int | None = None


class ResourceSlotsOut(BaseModel):
    resource_id: uuid.UUID
    resource_name: str
    resource_type: ResourceType
    timezone: str
    duration_minutes: int
    price: Decimal | None
    booking_type: BookingType
    slots: list[SlotOut]


class AggregatedSlotOut(BaseModel):
    start: datetime
    end: datetime
    start_time: str
    end_time: str
    available: bool
    resource_ids: list[uuid.UUID]


class AvailabilityOut(BaseModel):
    date: date
    service_id: uuid.UUID
    resources: list[ResourceSlotsOut]
    slots: list[AggregatedSlotOut]


class AlternativeOut(BaseModel):
    resource_id: uuid.UUID
    resource_name: str
    timezone: str
    start: datetime
    end: datetime
    same_resource: bool
    remaining: int | None = None


# ---------------------------------------------------------------- bookings


class BookingCreateIn(BaseModel):
    service_id: uuid.UUID
    # Omit to let the system pick any suitable resource that is free.
    resource_id: uuid.UUID | None = None
    start: datetime
    quantity: int = Field(default=1, ge=1, le=1000)
    notes: str | None = Field(default=None, max_length=2000)
    additional_resource_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    join_waitlist: bool = False
    # For services with a flexible length; omit for the service's own length.
    duration_minutes: int | None = Field(default=None, gt=0, le=24 * 60)


class AdminBookingCreateIn(BookingCreateIn):
    user_id: uuid.UUID
    override_rules: bool = False


class BookingInviteIn(BookingCreateIn):
    """A booking for an e-mail address that has no account yet."""

    customer_email: EmailStr
    override_rules: bool = False


class InvitationOut(ORM):
    id: uuid.UUID
    email: str
    expires_at: datetime


class InvitationPublicOut(BaseModel):
    """What the invitee sees before answering; the token in the link is the credential."""

    email: str
    # PENDING / ACCEPTED / DECLINED / FAILED, or EXPIRED for a pending one past its date.
    status: str
    invited_by: str
    service_name: str
    resource_name: str | None
    when: str
    expires_at: datetime
    has_account: bool
    # True when the booking is already on the calendar (declining cancels it).
    booked: bool = False


class InvitationAcceptIn(BaseModel):
    # Only needed when the e-mail has no account yet.
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: Phone = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class InvitationAcceptOut(BaseModel):
    booking_id: uuid.UUID | None
    # Why the booking could not be made (the account exists regardless).
    error: str | None = None
    signed_in: bool


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class RescheduleIn(BaseModel):
    start: datetime
    resource_id: uuid.UUID | None = None


class AdminRescheduleIn(RescheduleIn):
    override_rules: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class ReassignIn(BaseModel):
    resource_id: uuid.UUID
    override_rules: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class AdminBookingUpdateIn(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, ge=0)


class RecurringIn(BookingCreateIn):
    frequency: RecurrenceFrequency = RecurrenceFrequency.WEEKLY
    interval: int = Field(default=1, ge=1, le=52)
    count: int = Field(default=4, ge=1, le=366)
    custom_starts: list[datetime] = Field(default_factory=list, max_length=366)
    skip_conflicts: bool = False
    user_id: uuid.UUID | None = None
    override_rules: bool = False


class OccurrenceOut(BaseModel):
    start: datetime
    end: datetime
    available: bool
    code: str
    message: str


class RecurringPreviewOut(BaseModel):
    occurrences: list[OccurrenceOut]
    available_count: int
    conflict_count: int


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class UserRef(Ref):
    email: str


class ResourceRef(Ref):
    type: ResourceType


class ServiceRef(Ref):
    booking_type: BookingType
    duration_minutes: int


class BookingOut(BaseModel):
    id: uuid.UUID
    user: UserRef
    service: ServiceRef
    resource: ResourceRef
    additional_resources: list[ResourceRef]
    location: LocationRef | None
    timezone: str
    start_datetime: datetime
    end_datetime: datetime
    quantity: int
    status: BookingStatus
    notes: str | None
    price: Decimal | None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    completed_at: datetime | None = None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    conflict_reason: str | None
    rescheduled_from_id: uuid.UUID | None
    rescheduled_to_id: uuid.UUID | None
    recurring_series_id: uuid.UUID | None
    waitlist_position: int | None
    can_cancel: bool
    can_reschedule: bool
    # Made for a customer who has not confirmed it yet: only they can, the admin cannot.
    awaiting_customer: bool = False
    # Held by the booking assistant until the customer confirms or deletes it in the chat.
    held_in_chat: bool = False
    # Held until the customer pays for it online (or the payment window passes).
    awaiting_payment: bool = False
    payment: "PaymentRef | None" = None


class PaymentRef(BaseModel):
    id: uuid.UUID
    status: PaymentStatus
    amount: Decimal
    currency: str
    expires_at: datetime
    paid_at: datetime | None


class RecurringCreatedOut(BaseModel):
    series_id: uuid.UUID
    created: list[BookingOut]
    skipped: list[OccurrenceOut]


class ConflictResolveIn(BaseModel):
    action: Literal["override", "cancel", "reschedule", "reassign"]
    start: datetime | None = None
    resource_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=1000)
    override_rules: bool = False

    @model_validator(mode="after")
    def _needs(self) -> "ConflictResolveIn":
        if self.action == "reschedule" and self.start is None:
            raise ValueError("start is required to reschedule")
        if self.action == "reassign" and self.resource_id is None:
            raise ValueError("resource_id is required to reassign")
        return self


# ---------------------------------------------------------------- misc


class NotificationOut(ORM):
    id: uuid.UUID
    booking_id: uuid.UUID | None
    link: str | None = None
    type: NotificationType
    channel: NotificationChannel
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime


class AuditLogOut(ORM):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_name: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    created_at: datetime


class SettingsOut(BaseModel):
    values: dict[str, Any]
    # Per setting, a token that changes on every save (null while it has its default).
    versions: dict[str, str | None]
    definitions: list[dict[str, Any]]


class SettingsIn(BaseModel):
    # Only the settings being changed.
    values: dict[str, Any]
    # The versions the editor loaded for them; a setting saved by someone else since is refused.
    versions: dict[str, str | None] | None = None


class PublicConfigOut(BaseModel):
    business_name: str
    default_timezone: str
    allow_waitlist: bool
    allow_recurring_bookings: bool
    require_admin_confirmation: bool
    maximum_advance_booking_days: int
    minimum_booking_notice: int
    cancellation_window: int
    rescheduling_window: int


# ---------------------------------------------------------------- booking assistant


class ChatSubscriptionOut(BaseModel):
    active: bool
    activated_at: datetime | None = None
    paused_until: datetime | None = None
    # False until the operator has set the model and guard keys.
    configured: bool
    price_label: str = "9.99 / month"
    demo: bool = True


class ChatConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime


class ChatConversationDetailOut(ChatConversationOut):
    items: list[dict[str, Any]]
    starters: list[str]
    hold_minutes: int


class ChatMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ChatProposalConfirmIn(BaseModel):
    # A HANDOFF card: the description as the customer finally wrote it.
    summary: str | None = Field(default=None, max_length=600)


class ChatHelpIn(BaseModel):
    summary: str = Field(min_length=1, max_length=600)


class ChatFeedbackIn(BaseModel):
    value: Literal[1, -1] | None
