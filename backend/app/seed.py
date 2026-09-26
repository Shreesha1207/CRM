"""Demo data: `python -m app.seed`.

Creates an admin, a demo user and a small multi-industry catalogue (a
trainer, a consultant, a meeting room, a tennis court and a group class) to
show that one engine handles all of them. Safe to run twice.
"""

import os
from datetime import time
from decimal import Decimal

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import (
    Location,
    OperatingHours,
    Resource,
    ResourceAvailability,
    ResourceService,
    Service,
    User,
)
from app.models.enums import BookingType, ResourceType, Role
from app.services import settings_service

ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "admin12345")
USER_EMAIL = "user@example.com"
USER_PASSWORD = "user12345"


def weekly(resource: Resource, days: range, start: time, end: time) -> list[ResourceAvailability]:
    return [
        ResourceAvailability(resource=resource, day_of_week=d, start_time=start, end_time=end) for d in days
    ]


def run() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == ADMIN_EMAIL)):
            print("Seed data already present.")
            return

        tz = os.getenv("SEED_TIMEZONE", "Asia/Kolkata")
        settings_service.update(db, {"default_timezone": tz, "business_name": "Acme Bookings"}, None)

        db.add_all(
            [
                User(name="Admin", email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD), role=Role.ADMIN),
                User(name="Demo User", email=USER_EMAIL, password_hash=hash_password(USER_PASSWORD), role=Role.USER),
            ]
        )

        downtown = Location(name="Downtown", address="12 MG Road", timezone=tz)
        riverside = Location(name="Riverside Sports Club", address="4 River Lane", timezone=tz)
        db.add_all([downtown, riverside])
        db.flush()

        # Business hours: Mon-Sat 09:00-18:00 downtown, 07:00-22:00 at the club; Sunday closed.
        for d in range(6):
            db.add(OperatingHours(location_id=downtown.id, day_of_week=d, start_time=time(9), end_time=time(18)))
        for d in range(7):
            db.add(OperatingHours(location_id=riverside.id, day_of_week=d, start_time=time(7), end_time=time(22)))

        # Prices are hourly rates. Individual services can be booked for longer
        # than their duration, in steps of it, up to max_duration_minutes.
        training = Service(name="Personal Training", description="One-to-one strength and conditioning.", duration_minutes=60, max_duration_minutes=120, price=Decimal("40.00"), buffer_after=15)
        consultation = Service(name="Consultation", description="A consultation, from 30 minutes to 2 hours.", duration_minutes=30, max_duration_minutes=120, price=Decimal("50.00"))
        meeting = Service(name="Meeting Room", description="Private meeting room with screen.", duration_minutes=60, max_duration_minutes=240, price=Decimal("15.00"))
        tennis = Service(name="Tennis Court", description="Outdoor hard court.", duration_minutes=60, max_duration_minutes=180, price=Decimal("20.00"))
        yoga = Service(name="Yoga Class", description="Group vinyasa flow class.", duration_minutes=60, price=Decimal("12.00"), booking_type=BookingType.CAPACITY, capacity=12)
        db.add_all([training, consultation, meeting, tennis, yoga])

        john = Resource(name="John Carter", type=ResourceType.PERSON, location=downtown, description="Strength coach.", attributes={"specialization": "Strength Training", "experience": 8})
        priya = Resource(name="Priya Nair", type=ResourceType.PERSON, location=downtown, description="Consultant and coach.", attributes={"specialization": "Nutrition", "experience": 5})
        room_a = Resource(name="Meeting Room A", type=ResourceType.ROOM, location=downtown, capacity=10, attributes={"floor": 3, "projector": True})
        court_2 = Resource(name="Tennis Court 2", type=ResourceType.COURT, location=riverside, capacity=4, attributes={"surface": "hard", "floodlights": True})
        studio = Resource(name="Yoga Studio", type=ResourceType.ROOM, location=riverside, capacity=15, attributes={"mats_provided": True})
        db.add_all([john, priya, room_a, court_2, studio])
        db.flush()

        db.add_all(
            [
                ResourceService(resource=john, service=training),
                ResourceService(resource=john, service=consultation),
                ResourceService(resource=priya, service=consultation),
                ResourceService(resource=priya, service=training, custom_price=Decimal("45.00")),
                ResourceService(resource=room_a, service=meeting),
                ResourceService(resource=court_2, service=tennis),
                ResourceService(resource=studio, service=yoga),
            ]
        )

        # John: split shift with a lunch break. Priya: short days, off Wednesdays.
        db.add_all(weekly(john, range(0, 5), time(9), time(13)))
        db.add_all(weekly(john, range(0, 5), time(14), time(18)))
        db.add_all(weekly(priya, range(0, 6), time(10), time(16)))
        db.add(ResourceAvailability(resource=priya, day_of_week=2, start_time=time(10), end_time=time(16), is_available=False))
        # The yoga class runs weekday evenings and Saturday mornings.
        db.add_all(weekly(studio, range(0, 5), time(18), time(19)))
        db.add(ResourceAvailability(resource=studio, day_of_week=5, start_time=time(8), end_time=time(10)))
        # Meeting room and tennis court follow their location's operating hours.
        db.commit()
        print(f"Seeded. Admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD}   User: {USER_EMAIL} / {USER_PASSWORD}")


if __name__ == "__main__":
    run()
