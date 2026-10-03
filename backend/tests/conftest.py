"""Tests run against a real PostgreSQL database: row locks, the exclusion
constraint and timezone handling are exactly what needs verifying."""

import os

# The suite drops the whole schema, so it reads TEST_DATABASE_URL and never
# DATABASE_URL, which may point at a real (e.g. hosted) database.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://booking:booking@localhost:5432/booking_test"
)
os.environ["RUN_BACKGROUND_JOBS"] = "false"
# Tests move the frozen clock by days; keep sessions alive across that.
os.environ["ACCESS_TOKEN_TTL_MINUTES"] = str(60 * 24 * 60)
os.environ["EMAIL_BACKEND"] = "console"

from collections.abc import Iterator  # noqa: E402
from datetime import UTC, datetime, time  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password, rate_limiter  # noqa: E402
from app.core.timeutils import freeze_time  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Location,
    OperatingHours,
    Resource,
    ResourceAvailability,
    ResourceService,
    Service,
    User,
)
from app.models.enums import BookingType, ResourceType, Role  # noqa: E402
from app.services import settings_service  # noqa: E402

# A Monday. Frozen "now" for most tests is the Friday before, 08:00 UTC.
MONDAY = datetime(2027, 3, 1).date()
NOW = datetime(2027, 2, 26, 8, 0, tzinfo=UTC)

PASSWORD = "password123"


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    rate_limiter.reset()
    freeze_time(NOW)
    yield
    freeze_time(None)


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class Factory:
    """Small helpers that build a realistic catalogue in few lines."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def user(self, email: str = "user@test.io", role: Role = Role.USER, name: str = "Test User") -> User:
        user = User(name=name, email=email, password_hash=hash_password(PASSWORD), role=role)
        self.db.add(user)
        self.db.commit()
        return user

    def location(self, tz: str = "UTC", name: str = "HQ") -> Location:
        loc = Location(name=name, timezone=tz)
        self.db.add(loc)
        self.db.commit()
        return loc

    def service(self, name: str = "Consultation", duration: int = 60, **kw) -> Service:
        svc = Service(name=name, duration_minutes=duration, price=kw.pop("price", Decimal("10")), **kw)
        self.db.add(svc)
        self.db.commit()
        return svc

    def resource(
        self,
        name: str = "Resource A",
        *,
        services: list[Service] = (),
        location: Location | None = None,
        hours: list[tuple[int, time, time]] | None = None,
        type: ResourceType = ResourceType.PERSON,
        capacity: int | None = None,
    ) -> Resource:
        res = Resource(name=name, type=type, location_id=location.id if location else None, capacity=capacity)
        self.db.add(res)
        self.db.flush()
        for svc in services:
            self.db.add(ResourceService(resource_id=res.id, service_id=svc.id))
        for day, start, end in hours or []:
            self.db.add(ResourceAvailability(resource_id=res.id, day_of_week=day, start_time=start, end_time=end))
        self.db.commit()
        self.db.refresh(res)
        return res

    def weekdays(self, start: time, end: time) -> list[tuple[int, time, time]]:
        return [(d, start, end) for d in range(5)]

    def operating_hours(self, rows: list[tuple[int, time, time]], location: Location | None = None) -> None:
        for day, start, end in rows:
            self.db.add(OperatingHours(location_id=location.id if location else None, day_of_week=day, start_time=start, end_time=end))
        self.db.commit()

    def settings(self, **values) -> None:
        settings_service.update(self.db, values, None)
        self.db.commit()


@pytest.fixture
def make(db: Session) -> Factory:
    return Factory(db)


def auth_headers(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def user(make: Factory) -> User:
    return make.user()


@pytest.fixture
def admin(make: Factory) -> User:
    return make.user("admin@test.io", Role.ADMIN, name="Admin")


@pytest.fixture
def user_headers(client: TestClient, user: User) -> dict[str, str]:
    return auth_headers(client, user.email)


@pytest.fixture
def admin_headers(client: TestClient, admin: User) -> dict[str, str]:
    return auth_headers(client, admin.email)


@pytest.fixture
def basic(make: Factory):
    """One 60-minute service on one resource available weekdays 09:00-17:00 UTC."""
    service = make.service()
    resource = make.resource(services=[service], hours=make.weekdays(time(9), time(17)))
    return service, resource


def at(day, hh: int, mm: int = 0) -> str:
    """Naive ISO wall-clock time, as the UI sends it."""
    return datetime.combine(day, time(hh, mm)).isoformat()


__all__ = ["BookingType", "MONDAY", "NOW", "PASSWORD", "at", "auth_headers"]
