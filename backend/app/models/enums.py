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
    # A customer asked the admin to contact them from the booking assistant.
    CHAT_HELP_REQUESTED = "CHAT_HELP_REQUESTED"
    CHAT_HELP_OVERDUE = "CHAT_HELP_OVERDUE"
    # To the customer: the request was received, and later resolved.
    CHAT_HELP_RECEIVED = "CHAT_HELP_RECEIVED"
    CHAT_HELP_RESOLVED = "CHAT_HELP_RESOLVED"
    # The number of places (people) of a booking changed.
    BOOKING_UPDATED = "BOOKING_UPDATED"
    # Security notice: an AI assistant was connected to the account, or a token made for one.
    AGENT_CONNECTED = "AGENT_CONNECTED"
    # A paid booking was cancelled (to the customer and the admins), and the refund was made.
    PAYMENT_REFUND_DUE = "PAYMENT_REFUND_DUE"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"


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
    # A tool's result, answering one of the model's tool calls.
    TOOL = "tool"
    # Shown to the customer but never sent to the model (e.g. the fixed reply
    # to a blocked message).
    NOTICE = "notice"


class ChatProposalKind(StrEnum):
    # A booking the assistant made and holds for the customer to confirm or delete.
    HOLD = "HOLD"
    # Changes to an existing booking, made only when the customer confirms.
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    # How many people an existing booking is for (payload: quantity, from).
    PLACES = "PLACES"
    # Send the customer's problem to the admin, only when the customer confirms.
    HANDOFF = "HANDOFF"


class ChatHelpStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ChatProposalStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class AgentConnectionKind(StrEnum):
    # An AI app the customer approved on the consent page (OAuth).
    OAUTH = "OAUTH"
    # A personal access token the customer made, e.g. for a Siri Shortcut.
    TOKEN = "TOKEN"


class AgentTokenKind(StrEnum):
    # A one-time authorization code, exchanged for the first tokens.
    CODE = "CODE"
    ACCESS = "ACCESS"
    REFRESH = "REFRESH"


class PaymentStatus(StrEnum):
    # Waiting for the customer to pay; the booking holds its time until expires_at.
    PENDING = "PENDING"
    PAID = "PAID"
    # Not paid in time: the booking was released.
    EXPIRED = "EXPIRED"
    # The customer gave up before paying.
    CANCELLED = "CANCELLED"
    # Paid, then the booking was cancelled: the business owes a refund.
    REFUND_DUE = "REFUND_DUE"
    REFUNDED = "REFUNDED"
