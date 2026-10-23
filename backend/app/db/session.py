from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_unreachable(exc: BaseException) -> bool:
    """True when the database could not be reached at all (its host doesn't resolve, the network
    or the server is down), as opposed to a query failing on a working connection: errors from
    the server itself carry a SQLSTATE, connection failures don't."""
    return isinstance(exc, DBAPIError) and (exc.connection_invalidated or getattr(exc.orig, "sqlstate", None) is None)
