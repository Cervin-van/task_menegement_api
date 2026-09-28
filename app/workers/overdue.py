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

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import SessionFactory, engine
from app.domain.status import TaskStatus
from app.models import Task
from app.repositories.task import overdue_condition

logger = logging.getLogger("app.workers.overdue")

# Arbitrary app-wide constant identifying this job's advisory lock.
ADVISORY_LOCK_KEY = 7_340_001

SessionFactoryType = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def cancel_overdue(session: AsyncSession, now: datetime) -> list[int]:
    """One pass: cancels all unfinished tasks whose deadline has passed. Returns their ids.

    - pg_try_advisory_xact_lock: non-blocking; released automatically on commit/rollback,
      so a crashed worker can't leave a stale lock.
    - Single bulk UPDATE ... RETURNING instead of load-and-save per row.
    - If a user holds FOR UPDATE on a task (e.g. moving it to done), this UPDATE waits and,
      under READ COMMITTED, re-checks WHERE on the new row version -> finished task is skipped.
    """
    acquired = await session.scalar(select(func.pg_try_advisory_xact_lock(ADVISORY_LOCK_KEY)))
    if not acquired:
        # Nothing was written; the caller's session scope ends the transaction.
        logger.info("overdue.skip another worker holds the lock")
        return []

    result = await session.execute(
        update(Task)
        .where(overdue_condition(now))
        .values(status=TaskStatus.CANCELLED)
        .returning(Task.id)
        .execution_options(synchronize_session=False)
    )
    ids = list(result.scalars())
    await session.commit()

    if ids:
        logger.info("overdue.cancelled count=%d task_ids=%s", len(ids), ids)
    return ids


async def run_worker(
    session_factory: SessionFactoryType, interval: float, stop: asyncio.Event
) -> None:
    logger.info("overdue.worker started interval=%ss", interval)
    while not stop.is_set():
        try:
            async with session_factory() as session:
                await cancel_overdue(session, datetime.now(UTC))
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
