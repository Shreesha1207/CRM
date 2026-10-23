"""When the database can't be reached (its host doesn't resolve, the network is down), requests
get a clear 503 and the logs get one line each, not a traceback; real query errors still fail
as before."""

import asyncio
import logging
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import database_unreachable, get_db
from app.jobs import runner
from app.main import app

# ".invalid" never resolves, like a database host that has gone away.
dead_engine = create_engine("postgresql+psycopg://booking:booking@database.invalid:5432/booking", connect_args={"connect_timeout": 2})


@pytest.fixture
def database_down() -> Iterator[None]:
    def dead_db() -> Iterator[Session]:
        with sessionmaker(bind=dead_engine)() as db:
            yield db

    app.dependency_overrides[get_db] = dead_db
    yield
    app.dependency_overrides.pop(get_db)


def test_requests_get_a_clear_503(client, user_headers, database_down, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/auth/session", headers=user_headers)
    assert response.status_code == 503 and response.headers["retry-after"] == "30"
    assert response.json()["code"] == "DATABASE_UNAVAILABLE" and "try again" in response.json()["detail"]
    [line] = [r for r in caplog.records if "can't be reached" in r.getMessage()]
    assert "GET /api/auth/session" in line.getMessage() and "database.invalid" in line.getMessage() and line.exc_info is None


def test_background_jobs_log_one_line(monkeypatch, caplog) -> None:
    monkeypatch.setattr(runner, "engine", dead_engine)

    async def stop(_: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(runner.asyncio, "sleep", stop)
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.run_forever())
    [line] = caplog.records
    assert line.getMessage().startswith("Background jobs skipped: the database can't be reached") and line.exc_info is None


def test_a_query_error_on_a_working_connection_is_not_an_outage(db) -> None:
    with pytest.raises(DBAPIError) as failed:
        db.execute(text("SELECT 1 / 0"))
    assert not database_unreachable(failed.value)
