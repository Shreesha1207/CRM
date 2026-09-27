import os
import uuid
from decimal import Decimal

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.core.config import get_settings
from app.db.session import engine


def alembic_config() -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def prices():
    with engine.connect() as conn:
        services = dict(conn.execute(text("SELECT name, price FROM services")).all())
        links = dict(conn.execute(text("SELECT r.name, rs.custom_price FROM resource_services rs JOIN resources r ON r.id = rs.resource_id")).all())
    return services, links


def test_0002_turns_session_prices_into_hourly_rates_and_back() -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "0001")
    try:
        half_hour, ninety, free = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO services (id, name, duration_minutes, price, booking_type, status) VALUES "
                    "(:a, 'Half hour', 30, 25.00, 'INDIVIDUAL', 'ACTIVE'), "
                    "(:b, 'Ninety', 90, 30.00, 'INDIVIDUAL', 'ACTIVE'), "
                    "(:c, 'Free', 60, NULL, 'INDIVIDUAL', 'ACTIVE')"
                ),
                {"a": half_hour, "b": ninety, "c": free},
            )
            for name, service, price, duration in (("Own length", half_hour, "45.00", 90), ("Service length", half_hour, "20.00", None)):
                resource = uuid.uuid4()
                conn.execute(
                    text("INSERT INTO resources (id, name, type, metadata, status) VALUES (:id, :name, 'PERSON', '{}', 'ACTIVE')"),
                    {"id": resource, "name": name},
                )
                conn.execute(
                    text(
                        "INSERT INTO resource_services (resource_id, service_id, custom_price, custom_duration, status) "
                        "VALUES (:r, :s, :p, :d, 'ACTIVE')"
                    ),
                    {"r": resource, "s": service, "p": price, "d": duration},
                )

        command.upgrade(cfg, "0002")
        # Each price becomes the hourly rate that keeps a standard-length booking's total.
        assert prices() == (
            {"Half hour": Decimal("50.00"), "Ninety": Decimal("20.00"), "Free": None},
            {"Own length": Decimal("30.00"), "Service length": Decimal("40.00")},
        )

        command.downgrade(cfg, "0001")
        assert prices() == (
            {"Half hour": Decimal("25.00"), "Ninety": Decimal("30.00"), "Free": None},
            {"Own length": Decimal("45.00"), "Service length": Decimal("20.00")},
        )
    finally:
        command.upgrade(cfg, "head")
