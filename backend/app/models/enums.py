from enum import StrEnum


class Role(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    # Reserved for future use; the permission map already knows about them.
    STAFF = "STAFF"
    RESOURCE_OWNER = "RESOURCE_OWNER"
    MANAGER = "MANAGER"
    SUPER_ADMIN = "SUPER_ADMIN"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    # Made by an admin's booking invitation: has no usable password and cannot
    # sign in until the person accepts the invitation and sets one.
    INVITED = "INVITED"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


class RecordStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ResourceType(StrEnum):
    PERSON = "PERSON"
    ROOM = "ROOM"
    FACILITY = "FACILITY"
    EQUIPMENT = "EQUIPMENT"
    VEHICLE = "VEHICLE"
    DESK = "DESK"
    COURT = "COURT"
    CUSTOM = "CUSTOM"


class BookingType(StrEnum):
    # One booking holds the resource exclusively.
    INDIVIDUAL = "INDIVIDUAL"
    # Many bookings share one session up to a capacity.
    CAPACITY = "CAPACITY"


class ExceptionType(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"
    SPECIAL_HOURS = "SPECIAL_HOURS"
    HOLIDAY = "HOLIDAY"
    MAINTENANCE = "MAINTENANCE"


# Exception types that remove time. SPECIAL_HOURS instead *replaces* the
# recurring schedule for the dates it touches.
BLOCKING_EXCEPTION_TYPES = frozenset(
    {
        ExceptionType.UNAVAILABLE,
        ExceptionType.BLOCKED,
        ExceptionType.HOLIDAY,
        ExceptionType.MAINTENANCE,
    }
)


class BookingStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"
    NO_SHOW = "NO_SHOW"
    WAITLISTED = "WAITLISTED"
    CONFLICTED = "CONFLICTED"


class NotificationChannel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class NotificationType(StrEnum):
    BOOKING_CREATED = "BOOKING_CREATED"
    BOOKING_CONFIRMED = "BOOKING_CONFIRMED"
    BOOKING_RESCHEDULED = "BOOKING_RESCHEDULED"
    BOOKING_CANCELLED = "BOOKING_CANCELLED"
    BOOKING_REMINDER = "BOOKING_REMINDER"
    WAITLIST_PROMOTION = "WAITLIST_PROMOTION"
    RESOURCE_CHANGED = "RESOURCE_CHANGED"
    BOOKING_CONFLICTED = "BOOKING_CONFLICTED"
    PASSWORD_RESET = "PASSWORD_RESET"
    INVITATION_ACCEPTED = "INVITATION_ACCEPTED"
    INVITATION_DECLINED = "INVITATION_DECLINED"


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    # The customer registered, but the time was no longer free.
    FAILED = "FAILED"
    # Replaced by a newer invitation (the booking was moved), so its link is dead.
    WITHDRAWN = "WITHDRAWN"


class RecurrenceFrequency(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class ChatSubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


class ChatRole(StrEnum):
    # The roles of the model's conversation, spelled as the API spells them.
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    # Shown to the customer but never sent to the model (e.g. the fixed reply
    # to a blocked message).
    NOTICE = "notice"


class ChatProposalKind(StrEnum):
    # A booking the assistant made and holds for the customer to confirm or delete.
    HOLD = "HOLD"
    # Changes to an existing booking, made only when the customer confirms.
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"


class ChatProposalStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
