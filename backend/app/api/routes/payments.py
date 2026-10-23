"""Online payment for bookings: the customer's checkout page, the provider's
callback, and refunds for the admins. See app/services/payments.py."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.api.deps import require as require_permission
from app.core.errors import NotFound
from app.core.permissions import Permission
from app.jobs.runner import dispatch_notifications
from app.models import Payment, User
from app.models.enums import PaymentStatus
from app.services import payments
from app.services.booking_engine import BookingEngine

router = APIRouter(prefix="/api/payments", tags=["payments"])
admin_router = APIRouter(prefix="/api/admin/payments", tags=["admin"])


@router.get("/{payment_id}")
def get_payment(payment_id: uuid.UUID, user: CurrentUser, db: DB) -> dict:
    return payments.out(db, payments.own(db, payment_id, user))


@router.post("/{payment_id}/demo/complete")
def pay_demo(payment_id: uuid.UUID, user: CurrentUser, db: DB, background: BackgroundTasks) -> dict:
    """The demo gateway's Pay button: no money moves. Gone once a real gateway is configured."""
    if not payments.is_demo():
        raise NotFound("Payment")
    payment = payments.own(db, payment_id, user)
    payment = payments.mark_paid(db, payment.id, f"demo_{uuid.uuid4().hex[:16]}")
    background.add_task(dispatch_notifications)
    return payments.out(db, payment)


@router.post("/{payment_id}/cancel")
def cancel_payment(payment_id: uuid.UUID, user: CurrentUser, db: DB) -> dict:
    """The customer gives up before paying: the booking's time is released."""
    payment = payments.own(db, payment_id, user)
    booking = payments.booking_of(db, payment)
    if booking is None:
        raise NotFound("Payment")
    BookingEngine(db, user).release_unpaid(booking.id, reason="Payment cancelled")
    return payments.out(db, db.get(Payment, payment_id))


async def _raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/webhook/{provider_name}")
def provider_callback(
    provider_name: str, request: Request, body: Annotated[bytes, Depends(_raw_body)], db: DB, background: BackgroundTasks
) -> dict:
    """Where the payment provider says a payment went through. Only its signature counts."""
    gateway = payments.provider()
    if provider_name != gateway.name:
        raise NotFound("Payment provider")
    event = gateway.parse_webhook(body, request.headers)
    if event.paid:
        payments.mark_paid(db, event.payment_id, event.provider_ref)
        background.add_task(dispatch_notifications)
    return {"received": True}


# ---------------------------------------------------------------- refunds (admins)


class RefundedIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _admin_out(db, payment: Payment) -> dict:
    customer = db.get(User, payment.user_id) if payment.user_id else None
    return {
        **payments.out(db, payment),
        "customer": {"id": str(customer.id), "name": customer.name, "email": customer.email} if customer else None,
        "refund_due_at": payment.refund_due_at.isoformat() if payment.refund_due_at else None,
        "refunded_at": payment.refunded_at.isoformat() if payment.refunded_at else None,
        "refund_note": payment.refund_note,
    }


@admin_router.get("")
def list_payments(
    db: DB,
    _: User = Depends(require_permission(Permission.BOOKINGS_MANAGE)),
    status: PaymentStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    stmt = select(Payment).order_by(Payment.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Payment.status == status)
    return [_admin_out(db, p) for p in db.scalars(stmt)]


@admin_router.post("/{payment_id}/refunded")
def mark_refunded(
    payment_id: uuid.UUID,
    body: RefundedIn,
    db: DB,
    background: BackgroundTasks,
    admin: User = Depends(require_permission(Permission.BOOKINGS_MANAGE)),
) -> dict:
    payment = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if payment is None:
        raise NotFound("Payment")
    payments.mark_refunded(db, payment, admin, body.note)
    background.add_task(dispatch_notifications)
    return _admin_out(db, payment)
