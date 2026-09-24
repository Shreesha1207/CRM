"""Race-condition protection: simultaneous requests for the same slot."""

import threading
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import BookingRejected
from app.db.session import SessionLocal
from app.models import Booking, BookingResource, User
from app.models.enums import BookingStatus, BookingType
from app.services.booking_engine import BookingEngine, BookingRequest
from tests.conftest import MONDAY


def race(n: int, attempt) -> list[object]:
    """Run ``attempt`` in n threads released at the same instant."""
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = attempt(i)
        except Exception as exc:  # collected and asserted on below
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def _book(user_id, service_id, resource_id, start, **kw):
    with SessionLocal() as db:
        actor = db.get(User, user_id)
        booking = BookingEngine(db, actor).create(
            BookingRequest(user_id=user_id, service_id=service_id, resource_id=resource_id, start=start, **kw)
        )
        return booking.id


def test_only_one_of_many_simultaneous_bookings_succeeds(make, basic) -> None:
    service, resource = basic
    users = [make.user(f"racer{i}@test.io") for i in range(10)]
    start = datetime.combine(MONDAY, time(10))

    results = race(10, lambda i: _book(users[i].id, service.id, resource.id, start))

    successes = [r for r in results if isinstance(r, uuid.UUID)]
    failures = [r for r in results if isinstance(r, BookingRejected)]
    assert len(successes) == 1, results
    assert len(failures) == 9 and {f.code for f in failures} == {"CONFLICT"}


def test_overlapping_but_different_times_also_race_safely(make, basic) -> None:
    service, resource = basic
    users = [make.user(f"racer{i}@test.io") for i in range(6)]
    starts = [datetime.combine(MONDAY, time(10, 10 * i)) for i in range(6)]  # 10:00..10:50, all overlap

    results = race(6, lambda i: _book(users[i].id, service.id, resource.id, starts[i]))
    assert sum(isinstance(r, uuid.UUID) for r in results) == 1, results


def test_capacity_is_never_exceeded_under_contention(make, db) -> None:
    service = make.service("Class", booking_type=BookingType.CAPACITY, capacity=3)
    resource = make.resource(services=[service], hours=[(0, time(18), time(19))])
    users = [make.user(f"racer{i}@test.io") for i in range(12)]
    start = datetime.combine(MONDAY, time(18))

    results = race(12, lambda i: _book(users[i].id, service.id, resource.id, start, join_waitlist=True))
    assert all(isinstance(r, uuid.UUID) for r in results), results

    statuses = [db.get(Booking, r).status for r in results]
    assert statuses.count(BookingStatus.CONFIRMED) == 3
    assert statuses.count(BookingStatus.WAITLISTED) == 9


def test_simultaneous_cancellations_promote_exactly_one_waitlisted_each(make, db) -> None:
    service = make.service("Class", booking_type=BookingType.CAPACITY, capacity=2)
    resource = make.resource(services=[service], hours=[(0, time(18), time(19))])
    users = [make.user(f"u{i}@test.io") for i in range(5)]
    start = datetime.combine(MONDAY, time(18))
    ids = [_book(u.id, service.id, resource.id, start, join_waitlist=True) for u in users]
    # u0, u1 confirmed; u2, u3, u4 waitlisted.

    def cancel(i: int):
        with SessionLocal() as s:
            return BookingEngine(s, s.get(User, users[i].id)).cancel(ids[i]).id

    race(2, cancel)
    db.expire_all()
    statuses = [db.get(Booking, i).status for i in ids]
    assert statuses[:2] == [BookingStatus.CANCELLED] * 2
    assert statuses[2:] == [BookingStatus.CONFIRMED, BookingStatus.CONFIRMED, BookingStatus.WAITLISTED]


def test_database_exclusion_constraint_is_a_backstop(make, basic, db) -> None:
    """Even writes that skip the application checks cannot double book."""
    service, resource = basic
    user = make.user()
    start = datetime(2027, 3, 1, 10, tzinfo=UTC)

    def raw_booking(offset: int, active: bool = True) -> Booking:
        b = Booking(
            id=uuid.uuid4(), user_id=user.id, service_id=service.id, primary_resource_id=resource.id,
            start_datetime=start + timedelta(minutes=offset), end_datetime=start + timedelta(minutes=offset + 60),
            quantity=1, status=BookingStatus.CONFIRMED,
        )
        b.allocations.append(
            BookingResource(
                resource_id=resource.id, occupied_start=b.start_datetime, occupied_end=b.end_datetime,
                allocation_key=f"x:{b.id}", active=active,
            )
        )
        return b

    db.add(raw_booking(0))
    db.commit()
    db.add(raw_booking(30))
    with pytest.raises(IntegrityError, match="ex_booking_resources_no_overlap"):
        db.commit()
    db.rollback()

    # Inactive (cancelled / rescheduled) allocations do not count.
    db.add(raw_booking(30, active=False))
    db.commit()
