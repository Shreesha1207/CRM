"""Online payment for bookings, behind a provider seam.

When an admin switches on `online_payments`, a customer's own booking is held as
PENDING (silently, like a booking the assistant holds) with a PENDING payment.
The customer pays on the provider's checkout page; the provider tells us, and the
booking is confirmed. Unpaid bookings are released after PAYMENT_WINDOW_MINUTES.
A paid booking that is cancelled becomes REFUND_DUE; an admin refunds it from the
provider's dashboard and marks it REFUNDED here.

Nothing outside this module knows which provider is used. The only one so far is
`DemoProvider`, which charges nothing. A real gateway is one more class with the
same two methods (see the README):

    checkout_url(payment)          where to send the customer to pay
    parse_webhook(body, headers)   check the provider's signature, say what was paid

Assistants never see card details: they only pass the checkout link on.
"""

import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.permissions import ROLE_PERMISSIONS, Permission
from app.core.timeutils import utcnow
from app.models import Booking, Payment, User
from app.models.enums import BookingStatus, NotificationChannel, NotificationType, PaymentStatus, UserStatus
from app.services import audit, events, settings_service
from app.services.events import DomainEvent
from app.services.notifications import notify

log = logging.getLogger("payments")

P = PaymentStatus


class WebhookRejected(AppError):
    status_code = 400


@dataclass(frozen=True)
class WebhookEvent:
    payment_id: uuid.UUID
    provider_ref: str
    paid: bool


class PaymentProvider(Protocol):
    name: str

    def checkout_url(self, payment: Payment) -> str: ...

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> WebhookEvent: ...


class DemoProvider:
    """Charges nothing. Its checkout is the CRM's own /pay page with a Pay (demo)
    button. Its webhook is signed like a real gateway's (HMAC-SHA256 of the body in
    X-Demo-Signature), so the path a real provider takes is the one that is tested."""

    name = "demo"

    def checkout_url(self, payment: Payment) -> str:
        return f"{get_settings().site_url}/pay/{payment.id}"

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> WebhookEvent:
        secret = get_settings().payment_webhook_secret
        if not secret:
            raise WebhookRejected("WEBHOOK_NOT_CONFIGURED", "Set PAYMENT_WEBHOOK_SECRET to accept payment callbacks")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.get("x-demo-signature", "")):
            raise WebhookRejected("BAD_SIGNATURE", "The payment callback's signature is not valid")
        try:
            data = json.loads(body)
            return WebhookEvent(uuid.UUID(data["payment_id"]), str(data["reference"])[:200], data.get("status") == "paid")
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookRejected("BAD_PAYLOAD", "The payment callback could not be read") from exc


PROVIDERS: dict[str, PaymentProvider] = {"demo": DemoProvider()}


def provider() -> PaymentProvider:
    name = get_settings().payment_provider
    if name not in PROVIDERS:
        raise RuntimeError(f"Unknown PAYMENT_PROVIDER {name!r}; known: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]


def is_demo() -> bool:
    return provider().name == "demo"


def checkout_url(payment: Payment) -> str:
    return provider().checkout_url(payment)


# ---------------------------------------------------------------- starting and finding payments


def takes_payment(db: Session) -> bool:
    return settings_service.get_rules(db).online_payments


def start(db: Session, booking: Booking, now: datetime) -> Payment:
    """A payment for a booking just held for it. Part of the caller's transaction."""
    payment = Payment(
        id=uuid.uuid4(),
        user_id=booking.user_id,
        amount=booking.price,
        currency=settings_service.get_rules(db).currency,
        status=P.PENDING,
        provider=provider().name,
        created_at=now,
        expires_at=now + timedelta(minutes=get_settings().payment_window_minutes),
    )
    db.add(payment)
    db.flush()
    booking.payment_id = payment.id
    return payment


def pending_for(db: Session, booking_id: uuid.UUID, *, lock: bool = False) -> Payment | None:
    """The payment a booking still waits for, if it does."""
    stmt = (
        select(Payment)
        .join(Booking, Booking.payment_id == Payment.id)
        .where(Booking.id == booking_id, Booking.status == BookingStatus.PENDING, Payment.status == P.PENDING)
    )
    return db.scalar(stmt.with_for_update(of=Payment) if lock else stmt)


def awaiting(db: Session, booking_ids: list) -> set:
    """Which of these bookings wait for their payment."""
    if not booking_ids:
        return set()
    return set(
        db.scalars(
            select(Booking.id)
            .join(Payment, Booking.payment_id == Payment.id)
            .where(Booking.id.in_(booking_ids), Booking.status == BookingStatus.PENDING, Payment.status == P.PENDING)
        )
    )


def booking_of(db: Session, payment: Payment) -> Booking | None:
    """The booking a payment is for: the latest of its reschedule chain."""
    return db.scalars(
        select(Booking).where(Booking.payment_id == payment.id).order_by(Booking.created_at.desc()).limit(1)
    ).first()


def own(db: Session, payment_id: uuid.UUID, user: User) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or payment.user_id != user.id:
        raise NotFound("Payment")
    return payment


def money(amount: Decimal | None, currency: str) -> str:
    return f"{amount:.2f} {currency}" if amount is not None else ""


def out(db: Session, payment: Payment, now: datetime | None = None) -> dict[str, Any]:
    from app.services import booking_mail

    now = now or utcnow()
    booking = booking_of(db, payment)
    status = P.EXPIRED if payment.status == P.PENDING and payment.expires_at <= now else payment.status
    result: dict[str, Any] = {
        "id": str(payment.id),
        "status": status.value,
        "amount": f"{payment.amount:.2f}",
        "currency": payment.currency,
        "provider": payment.provider,
        "demo": payment.provider == "demo",
        "expires_at": payment.expires_at.isoformat(),
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "checkout_url": checkout_url(payment) if status == P.PENDING else None,
        "booking_id": str(booking.id) if booking else None,
        "booking": None,
    }
    if booking is not None:
        facts = booking_mail.facts(db, booking, booking.user_id)
        result["booking"] = {
            "service": facts.service,
            "resource": facts.resource,
            "when": {"date": facts.when.date, "time": facts.when.time, "zone": facts.when.zone},
            "place": facts.place,
            "status": booking.status.value,
        }
    return result


# ---------------------------------------------------------------- paid, unpaid, refunds


def mark_paid(db: Session, payment_id: uuid.UUID, provider_ref: str) -> Payment:
    """The provider says the customer paid. Confirms the booking; safe to call twice.
    Money that arrives after the booking was released is owed back (REFUND_DUE). Commits."""
    from app.services.booking_engine import BookingEngine

    payment = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment is None:
        raise NotFound("Payment")
    if payment.status in (P.PAID, P.REFUND_DUE, P.REFUNDED):
        return payment  # told twice
    now = utcnow()
    payment.provider_ref, payment.paid_at = provider_ref, now
    booking = db.scalar(select(Booking).where(Booking.payment_id == payment.id, Booking.status == BookingStatus.PENDING))
    if payment.status != P.PENDING or booking is None:
        _owe(db, payment, now, "The payment arrived after the booking was released.")
        db.commit()
        return payment
    payment.status = P.PAID
    pid, bid = payment.id, booking.id
    owner = db.get(User, booking.user_id)
    try:
        BookingEngine(db, owner).confirm_paid(bid)
    except AppError as exc:
        db.rollback()
        log.warning("Payment %s arrived but booking %s could not be confirmed: %s", pid, bid, exc.message)
        payment = db.get(Payment, pid)
        payment.provider_ref, payment.paid_at = provider_ref, now
        _owe(db, payment, now, "The booking could no longer be confirmed.")
        db.commit()
    return db.get(Payment, pid)


def release_expired(db: Session, now: datetime) -> int:
    """Free the time of bookings nobody paid for in time. Returns how many were released."""
    from app.services.booking_engine import BookingEngine

    released = 0
    for payment in db.scalars(select(Payment).where(Payment.status == P.PENDING, Payment.expires_at <= now)).all():
        booking = db.scalar(select(Booking).where(Booking.payment_id == payment.id, Booking.status == BookingStatus.PENDING))
        owner = db.get(User, booking.user_id) if booking is not None else None
        if booking is None or owner is None:
            payment.status = P.EXPIRED
            db.commit()
            continue
        try:
            BookingEngine(db, owner, now=now).release_unpaid(booking.id, outcome=P.EXPIRED, reason="Not paid in time")
            released += 1
        except AppError:
            db.rollback()  # paid or cancelled meanwhile
    return released


def _staff_who_refund(db: Session) -> list[User]:
    roles = [role for role, granted in ROLE_PERMISSIONS.items() if Permission.BOOKINGS_MANAGE in granted]
    return list(db.scalars(select(User).where(User.role.in_(roles), User.status == UserStatus.ACTIVE)))


def _owe(db: Session, payment: Payment, now: datetime, why: str) -> None:
    """The business owes this payment back: tell the customer and the admins."""
    payment.status = P.REFUND_DUE
    payment.refund_due_at = now
    business = settings_service.get_rules(db).business_name
    amount = money(payment.amount, payment.currency)
    audit.record(db, actor_id=None, action="PAYMENT_REFUND_DUE", entity_type="payment", entity_id=payment.id, new={"amount": amount, "why": why})
    if payment.user_id is not None:
        notify(
            db,
            user_id=payment.user_id,
            type=NotificationType.PAYMENT_REFUND_DUE,
            title="Your payment will be refunded",
            body=f"{why} {amount} will be refunded to you by {business}.",
            link="/bookings",
            dedupe_key=f"refund-due:{payment.id}",
        )
    customer = db.get(User, payment.user_id) if payment.user_id else None
    for staff in _staff_who_refund(db):
        notify(
            db,
            user_id=staff.id,
            type=NotificationType.PAYMENT_REFUND_DUE,
            title=f"Refund due: {amount}",
            body=f"{why} Refund {amount} to {customer.name if customer else 'a deleted account'} from the payment provider, then mark it refunded.",
            channels=(NotificationChannel.IN_APP,),
            link="/admin/bookings?payment=REFUND_DUE",
            dedupe_key=f"refund-due:{payment.id}:{staff.id}",
        )


def _on_cancelled(db: Session, event: DomainEvent) -> None:
    booking: Booking = event.payload["booking"]
    if booking.payment_id is None:
        return
    payment = db.get(Payment, booking.payment_id)
    if payment is not None and payment.status == P.PAID:
        _owe(db, payment, utcnow(), "Your paid booking was cancelled.")


def register_handlers() -> None:
    events.subscribe("booking.cancelled", _on_cancelled)


def mark_refunded(db: Session, payment: Payment, admin: User, note: str | None) -> Payment:
    """An admin refunded it on the provider's side. Commits."""
    if payment.status != P.REFUND_DUE:
        raise AppError("NOT_REFUND_DUE", "Only payments waiting for a refund can be marked refunded", status_code=409)
    now = utcnow()
    payment.status = P.REFUNDED
    payment.refunded_at, payment.refunded_by = now, admin.id
    payment.refund_note = (note or "").strip() or None
    amount = money(payment.amount, payment.currency)
    audit.record(db, actor_id=admin.id, action="PAYMENT_REFUNDED", entity_type="payment", entity_id=payment.id, new={"amount": amount, "note": payment.refund_note})
    if payment.user_id is not None:
        business = settings_service.get_rules(db).business_name
        notify(
            db,
            user_id=payment.user_id,
            type=NotificationType.PAYMENT_REFUNDED,
            title="Refund issued",
            body=f"{business} has refunded {amount}." + (f"\nTheir note: {payment.refund_note}" if payment.refund_note else ""),
            link="/bookings",
            dedupe_key=f"refunded:{payment.id}",
        )
    db.commit()
    return payment
