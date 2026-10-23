"""Online payment for bookings, with the demo gateway: held until paid, released when
not, refunds owed when a paid booking is cancelled, and the provider's signed callback."""

import hashlib
import hmac
import json
import uuid
from datetime import time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.timeutils import freeze_time
from app.db.session import SessionLocal
from app.models import AuditLog, Booking, Notification, Payment, ResourceService
from app.models.enums import BookingStatus, NotificationChannel, NotificationType, PaymentStatus, Role
from app.services import payments
from tests.chat_fakes import reply_of
from tests.conftest import MONDAY, NOW, at, auth_headers
from tests.test_chat import HELD_REPLY, gym, hold_steps  # noqa: F401  (gym is a fixture)

S = BookingStatus


@pytest.fixture
def paid_service(make):
    """A 60-minute session at 30.00 an hour, weekdays 09:00-17:00 UTC."""
    make.settings(online_payments=True, currency="eur")
    service = make.service("Massage", duration=60, price=Decimal("30"))
    resource = make.resource("Room 1", services=[service], hours=make.weekdays(time(9), time(17)))
    return service, resource


def book(client, headers, service, resource, hour=10, day=MONDAY):
    response = client.post(
        "/api/bookings", headers=headers, json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(day, hour)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def emails(db) -> list[Notification]:
    db.expire_all()
    return list(db.scalars(select(Notification).where(Notification.channel == NotificationChannel.EMAIL)))


def pay(client, headers, payment_id):
    response = client.post(f"/api/payments/{payment_id}/demo/complete", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- off by default


def test_without_online_payments_bookings_are_confirmed_as_before(client, db, basic, user_headers) -> None:
    service, resource = basic
    booking = book(client, user_headers, service, resource)
    assert booking["status"] == "CONFIRMED" and booking["payment"] is None and booking["awaiting_payment"] is False
    assert db.scalar(select(Payment)) is None


# ---------------------------------------------------------------- paying


def test_a_booking_waits_for_its_payment_then_is_confirmed(client, db, paid_service, user_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    assert booking["status"] == "PENDING" and booking["awaiting_payment"] is True
    assert booking["can_cancel"] is True and booking["can_reschedule"] is False
    payment = booking["payment"]
    assert payment["status"] == "PENDING" and payment["amount"] == "30.00" and payment["currency"] == "EUR"
    assert emails(db) == []  # nobody is told before it is paid

    page = client.get(f"/api/payments/{payment['id']}", headers=user_headers).json()
    assert page["demo"] is True and page["checkout_url"].endswith(f"/pay/{payment['id']}")
    assert page["booking"]["service"] == "Massage" and page["booking"]["when"]["time"] == "10:00 AM – 11:00 AM"

    # The time is taken while the customer pays.
    other = auth_headers(client, _other(db).email)
    taken = client.post(
        "/api/bookings", headers=other, json={"service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)}
    )
    assert taken.status_code in (409, 422) and "PENDING" not in taken.text

    paid = pay(client, user_headers, payment["id"])
    assert paid["status"] == "PAID" and paid["checkout_url"] is None
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == S.CONFIRMED
    [mail] = emails(db)
    assert mail.title == "Booking confirmed"
    # Paying twice changes nothing.
    assert pay(client, user_headers, payment["id"])["status"] == "PAID"
    assert len(emails(db)) == 1


def _other(db):
    from app.core.security import hash_password
    from app.models import User
    from tests.conftest import PASSWORD

    user = User(name="Other", email="other@test.io", password_hash=hash_password(PASSWORD), role=Role.USER)
    db.add(user)
    db.commit()
    return user


def test_unpaid_bookings_are_released_when_the_window_passes(client, db, paid_service, user_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    freeze_time(NOW + timedelta(minutes=16))
    assert payments.release_expired(db, NOW + timedelta(minutes=16)) == 1
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == S.CANCELLED
    assert db.get(Payment, booking["payment"]["id"]).status == PaymentStatus.EXPIRED
    assert emails(db) == []
    # The time is free again.
    assert book(client, user_headers, service, resource)["status"] == "PENDING"


def test_cancelling_before_paying_just_releases_the_time(client, db, paid_service, user_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    response = client.post(f"/api/payments/{booking['payment']['id']}/cancel", headers=user_headers)
    assert response.json()["status"] == "CANCELLED"
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == S.CANCELLED
    assert emails(db) == []

    # Cancelling the booking itself does the same.
    second = book(client, user_headers, service, resource)
    assert client.post(f"/api/bookings/{second['id']}/cancel", headers=user_headers, json={}).status_code == 200
    db.expire_all()
    assert db.get(Payment, second["payment"]["id"]).status == PaymentStatus.CANCELLED


def test_money_that_arrives_after_the_release_is_owed_back(client, db, paid_service, user_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    payments.release_expired(db, NOW + timedelta(minutes=20))
    paid = pay(client, user_headers, booking["payment"]["id"])
    assert paid["status"] == "REFUND_DUE"
    db.expire_all()
    assert db.get(Booking, booking["id"]).status == S.CANCELLED


def test_a_cancelled_paid_booking_is_refunded_by_the_admin(client, db, paid_service, user_headers, admin_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource, day=MONDAY + timedelta(days=1))
    pay(client, user_headers, booking["payment"]["id"])
    assert client.post(f"/api/bookings/{booking['id']}/cancel", headers=user_headers, json={}).status_code == 200

    db.expire_all()
    payment = db.get(Payment, booking["payment"]["id"])
    assert payment.status == PaymentStatus.REFUND_DUE
    notices = {(n.type, n.channel) for n in db.scalars(select(Notification).where(Notification.type == NotificationType.PAYMENT_REFUND_DUE))}
    # The customer is told in the app and by e-mail; the admins in the app.
    assert notices == {(NotificationType.PAYMENT_REFUND_DUE, NotificationChannel.IN_APP), (NotificationType.PAYMENT_REFUND_DUE, NotificationChannel.EMAIL)}
    customer_mail = db.scalar(select(Notification).where(Notification.type == NotificationType.PAYMENT_REFUND_DUE, Notification.channel == NotificationChannel.EMAIL))
    assert "30.00 EUR will be refunded" in customer_mail.body

    due = client.get("/api/admin/payments?status=REFUND_DUE", headers=admin_headers).json()
    assert [p["id"] for p in due] == [str(payment.id)] and due[0]["customer"]["email"] == "user@test.io"
    assert client.get("/api/admin/payments", headers=user_headers).status_code == 403

    done = client.post(f"/api/admin/payments/{payment.id}/refunded", headers=admin_headers, json={"note": "Refunded on the gateway"})
    assert done.json()["status"] == "REFUNDED" and done.json()["refund_note"] == "Refunded on the gateway"
    assert client.post(f"/api/admin/payments/{payment.id}/refunded", headers=admin_headers, json={}).status_code == 409
    db.expire_all()
    assert db.scalar(select(AuditLog).where(AuditLog.action == "PAYMENT_REFUNDED")) is not None
    assert db.scalar(select(Notification).where(Notification.type == NotificationType.PAYMENT_REFUNDED)) is not None


def test_a_paid_booking_moves_only_to_the_same_price(client, db, make, paid_service, user_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    pay(client, user_headers, booking["payment"]["id"])

    moved = client.post(f"/api/bookings/{booking['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 12)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["payment"]["id"] == booking["payment"]["id"] and moved.json()["price"] == "30.00"

    pricier = make.resource("Room 2", services=[service], hours=make.weekdays(time(9), time(17)))
    db.execute(update(ResourceService).where(ResourceService.resource_id == pricier.id).values(custom_price=Decimal("50")))
    db.commit()
    refused = client.post(
        f"/api/bookings/{moved.json()['id']}/reschedule", headers=user_headers, json={"start": at(MONDAY, 14), "resource_id": str(pricier.id)}
    )
    assert refused.status_code == 422 and refused.json()["code"] == "PRICE_CHANGES"


def test_someone_elses_payment_is_not_found(client, db, paid_service, user_headers) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    other = auth_headers(client, _other(db).email)
    assert client.get(f"/api/payments/{booking['payment']['id']}", headers=other).status_code == 404
    assert client.post(f"/api/payments/{booking['payment']['id']}/demo/complete", headers=other).status_code == 404


def test_admin_bookings_for_a_customer_are_paid_at_the_venue(client, db, paid_service, user, admin_headers) -> None:
    service, resource = paid_service
    response = client.post(
        "/api/admin/bookings",
        headers=admin_headers,
        json={"user_id": str(user.id), "service_id": str(service.id), "resource_id": str(resource.id), "start": at(MONDAY, 10)},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "CONFIRMED" and response.json()["payment"] is None


# ---------------------------------------------------------------- the gateway's callback


def _signed(body: dict, secret: str = "whsec_test") -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    return raw, {"X-Demo-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), "Content-Type": "application/json"}


def test_the_providers_signed_callback_confirms_the_booking(client, db, paid_service, user_headers, settings) -> None:
    settings(payment_webhook_secret="whsec_test")
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    payload = {"payment_id": booking["payment"]["id"], "reference": "pay_123", "status": "paid"}

    raw, headers = _signed(payload, secret="wrong")
    assert client.post("/api/payments/webhook/demo", content=raw, headers=headers).status_code == 400
    assert client.post("/api/payments/webhook/stripe", content=raw, headers=headers).status_code == 404

    raw, headers = _signed(payload)
    assert client.post("/api/payments/webhook/demo", content=raw, headers=headers).json() == {"received": True}
    assert client.post("/api/payments/webhook/demo", content=raw, headers=headers).status_code == 200  # told twice
    db.expire_all()
    payment = db.get(Payment, booking["payment"]["id"])
    assert payment.status == PaymentStatus.PAID and payment.provider_ref == "pay_123"
    assert db.get(Booking, booking["id"]).status == S.CONFIRMED


def test_the_demo_button_is_gone_with_a_real_gateway(client, db, paid_service, user_headers, monkeypatch) -> None:
    service, resource = paid_service
    booking = book(client, user_headers, service, resource)
    monkeypatch.setattr(payments, "is_demo", lambda: False)
    assert client.post(f"/api/payments/{booking['payment']['id']}/demo/complete", headers=user_headers).status_code == 404


# ---------------------------------------------------------------- the assistant


def test_a_confirmed_chat_hold_is_paid_for_from_its_card(client, db, make, gym, chat) -> None:  # noqa: F811
    make.settings(online_payments=True)
    service, sam = gym
    [card] = reply_of(chat.send("Book me strength coaching with Sam next Tuesday at 10 for two hours", *hold_steps(service, sam)))["cards"]
    confirmed = chat.confirm(card["id"]).json()
    assert confirmed["status"] == "CONFIRMED" and confirmed["booking_status"] == "PENDING"
    payment = confirmed["payment"]
    assert payment["amount"] == "80.00" and payment["checkout_url"].endswith(f"/pay/{payment['id']}")
    assert emails(db) == []

    pay(client, chat.headers, payment["id"])
    db.expire_all()
    assert db.get(Booking, card["booking_id"]).status == S.CONFIRMED
    assert [m.title for m in emails(db)] == ["Booking confirmed"]
    with SessionLocal() as other:
        assert other.scalar(select(Payment.status).where(Payment.id == uuid.UUID(payment["id"]))) == PaymentStatus.PAID
