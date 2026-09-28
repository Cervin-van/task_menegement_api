"""Periodic auto-cancel of overdue tasks.

Runs as a separate process/container (`python -m app.workers.overdue`), not inside the API,
so scaling API replicas doesn't multiply the worker. If several workers run anyway,
a Postgres advisory lock lets only one of them do each pass.
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import SessionFactory, engine
from app.domain.events import TaskStatusChanged
from app.domain.status import TaskStatus
from app.models import Task, User
from app.notifications import dispatch_events
from app.repositories.task import overdue_condition

logger = logging.getLogger("app.workers.overdue")

# Arbitrary app-wide constant identifying this job's advisory lock.
ADVISORY_LOCK_KEY = 7_340_001

SessionFactoryType = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def cancel_overdue(session: AsyncSession, now: datetime) -> list[TaskStatusChanged]:
    """One pass: cancels all unfinished tasks whose deadline has passed.

    Returns status-change events (same as for manual changes) to notify author/assignee.

    - pg_try_advisory_xact_lock: non-blocking; released automatically on commit/rollback,
      so a crashed worker can't leave a stale lock.
    - Candidates are locked FOR NO KEY UPDATE SKIP LOCKED: a task a user is editing right now
      (e.g. moving it to done) is skipped, not waited for, and re-evaluated on the next pass.
    - One SELECT (with emails for notifications) + one bulk UPDATE, no per-row load-and-save.
    """
    acquired = await session.scalar(select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_KEY)))
    if not acquired:
        # Nothing was written; the caller's session scope ends the transaction.
        logger.info("overdue.skip another worker holds the lock")
        return []

    author, assignee = aliased(User), aliased(User)
    rows = (
        await session.execute(
            select(
                Task.id,
                Task.title,
                Task.status,
                author.email.label("author_email"),
                assignee.email.label("assignee_email"),
            )
            .join(author, Task.author_id == author.id)
            .outerjoin(assignee, Task.assignee_id == assignee.id)
            .where(overdue_condition(now))
            .with_for_update(of=Task, key_share=True, skip_locked=True)
        )
    ).all()
    if not rows:
        return []

    await session.execute(
        update(Task)
        .where(Task.id.in_([row.id for row in rows]))
        .values(status=TaskStatus.CANCELLED)
        .execution_options(synchronize_session=False)
    )
    await session.commit()

    logger.info("overdue.cancelled count=%d task_ids=%s", len(rows), [row.id for row in rows])
    return [
        TaskStatusChanged(
            task_id=row.id,
            title=row.title,
            old_status=row.status,
            new_status=TaskStatus.CANCELLED,
            recipients=tuple(sorted({e for e in (row.author_email, row.assignee_email) if e})),
        )
        for row in rows
    ]


async def run_worker(
    session_factory: SessionFactoryType, interval: float, stop: asyncio.Event
) -> None:
    logger.info("overdue.worker started interval=%ss", interval)
    while not stop.is_set():
        try:
            async with session_factory() as session:
                events = await cancel_overdue(session, datetime.now(UTC))
            # After commit and outside the DB session: delivery can't hold locks or connections.
            await dispatch_events(events)
        except Exception:  # a failed pass must not kill the loop
            logger.exception("overdue.pass failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    logger.info("overdue.worker stopped")


async def main() -> None:
    setup_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Graceful shutdown on `docker stop`; not supported on Windows event loop.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await run_worker(SessionFactory, settings.overdue_check_interval_seconds, stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
