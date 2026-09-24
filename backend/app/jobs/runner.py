"""Background jobs: reminder scheduling and notification delivery.

Runs inside the API process on an interval. A PostgreSQL advisory lock makes
sure only one process does the work when several replicas are running.
"""

import asyncio
import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.core.timeutils import utcnow
from app.db.session import SessionLocal, engine
from app.services import notifications

log = logging.getLogger("jobs")
JOB_LOCK_ID = 841_732_001


def dispatch_notifications() -> None:
    """Deliver queued notifications now (used right after a booking change)."""
    with SessionLocal() as db:
        try:
            notifications.dispatch_pending(db)
        except Exception:
            log.exception("Notification dispatch failed")


def run_once() -> None:
    # An advisory lock belongs to the connection that took it, and a Session
    # hands its connection back to the pool on every commit. So the lock is
    # taken and released on one dedicated connection (autocommit, so it never
    # sits idle in a transaction) while the work runs in its own Session.
    with engine.connect() as lock_conn:
        lock_conn.execution_options(isolation_level="AUTOCOMMIT")
        if not lock_conn.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": JOB_LOCK_ID}):
            return
        try:
            with SessionLocal() as db:
                notifications.queue_reminders(db, utcnow())
                notifications.dispatch_pending(db)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": JOB_LOCK_ID})


async def run_forever() -> None:
    interval = get_settings().job_interval_seconds
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            log.exception("Background job run failed")
        await asyncio.sleep(interval)
