"""Background jobs: reminder scheduling and notification delivery.

Runs inside the API process on an interval. A PostgreSQL advisory lock makes
sure only one process does the work when several replicas are running.
"""

import asyncio
import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.core.timeutils import utcnow
from app.db.session import SessionLocal
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
    with SessionLocal() as db:
        got_lock = db.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": JOB_LOCK_ID})
        if not got_lock:
            return
        try:
            notifications.queue_reminders(db, utcnow())
            notifications.dispatch_pending(db)
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": JOB_LOCK_ID})
            db.commit()


async def run_forever() -> None:
    interval = get_settings().job_interval_seconds
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:
            log.exception("Background job run failed")
        await asyncio.sleep(interval)
