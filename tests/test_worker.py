import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domain.status import TaskStatus
from app.models import Task
from app.workers.overdue import ADVISORY_LOCK_KEY, cancel_overdue, run_pass, run_worker
from tests.conftest import UserFactory

NOW = datetime.now(UTC)
PAST = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=1)


@pytest.fixture
async def author(create_user: UserFactory) -> dict[str, Any]:
    return await create_user()


async def seed(session: AsyncSession, author_id: int, **fields: Any) -> int:
    task = Task(title="Task", author_id=author_id, **fields)
    session.add(task)
    await session.flush()
    return task.id


async def status_of(session: AsyncSession, task_id: int) -> TaskStatus:
    # Column query: always reads the DB, ignoring stale identity-map objects.
    status = await session.scalar(select(Task.status).where(Task.id == task_id))
    assert status is not None
    return status


@pytest.mark.parametrize(
    "status",
    [TaskStatus.BACKLOG, TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW],
)
async def test_cancels_unfinished_overdue_task(
    session: AsyncSession, author: dict[str, Any], status: TaskStatus
) -> None:
    task_id = await seed(session, author["id"], status=status, deadline=PAST)

    events = await cancel_overdue(session, NOW)

    # Filter by own id: rows committed by other tests must not make this flaky.
    (event,) = [e for e in events if e.task_id == task_id]
    assert event.old_status is status
    assert event.new_status is TaskStatus.CANCELLED
    assert event.recipients == (author["email"],)
    assert await status_of(session, task_id) is TaskStatus.CANCELLED


async def test_leaves_other_tasks_untouched(session: AsyncSession, author: dict[str, Any]) -> None:
    untouched = {
        await seed(session, author["id"], status=TaskStatus.DONE, deadline=PAST): TaskStatus.DONE,
        await seed(
            session, author["id"], status=TaskStatus.CANCELLED, deadline=PAST
        ): TaskStatus.CANCELLED,
        await seed(session, author["id"], status=TaskStatus.TODO, deadline=FUTURE): TaskStatus.TODO,
        await seed(session, author["id"], status=TaskStatus.TODO): TaskStatus.TODO,
    }

    events = await cancel_overdue(session, NOW)

    assert not {e.task_id for e in events} & untouched.keys()
    for task_id, status in untouched.items():
        assert await status_of(session, task_id) is status


async def test_skips_pass_when_lock_is_held_elsewhere(
    engine: AsyncEngine, session: AsyncSession, author: dict[str, Any]
) -> None:
    task_id = await seed(session, author["id"], status=TaskStatus.TODO, deadline=PAST)

    async with engine.connect() as other_worker:
        await other_worker.execute(select(func.pg_advisory_lock(ADVISORY_LOCK_KEY)))
        try:
            cancelled = await cancel_overdue(session, NOW)
        finally:
            await other_worker.execute(select(func.pg_advisory_unlock(ADVISORY_LOCK_KEY)))

    assert cancelled == []
    assert await status_of(session, task_id) is TaskStatus.TODO


async def test_run_worker_processes_and_stops_gracefully(
    session: AsyncSession, author: dict[str, Any]
) -> None:
    task_id = await seed(session, author["id"], status=TaskStatus.IN_PROGRESS, deadline=PAST)
    stop = asyncio.Event()
    passes = 0

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        nonlocal passes
        passes += 1
        stop.set()  # stop after the first pass
        yield session

    await asyncio.wait_for(run_worker(session_factory, interval=60, stop=stop), timeout=5)

    assert passes == 1
    assert await status_of(session, task_id) is TaskStatus.CANCELLED


async def test_notifies_author_and_assignee(
    session: AsyncSession,
    create_user: UserFactory,
    author: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    assignee = await create_user()
    task_id = await seed(
        session,
        author["id"],
        status=TaskStatus.IN_PROGRESS,
        deadline=PAST,
        assignee_id=assignee["id"],
    )
    stop = asyncio.Event()

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        stop.set()
        yield session

    caplog.set_level(logging.INFO, logger="app.notifications")
    await asyncio.wait_for(run_worker(session_factory, interval=60, stop=stop), timeout=5)

    messages = [r.getMessage() for r in caplog.records if r.name == "app.notifications"]
    for email in (author["email"], assignee["email"]):
        assert any(
            f"task_id={task_id}" in m and email in m and "in_progress->cancelled" in m
            for m in messages
        )


async def test_run_pass_processes_all_batches(
    session: AsyncSession, author: dict[str, Any]
) -> None:
    ids = [
        await seed(session, author["id"], status=TaskStatus.TODO, deadline=PAST) for _ in range(5)
    ]

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield session

    cancelled = await run_pass(session_factory, batch_size=2)  # 2 + 2 + 1

    assert cancelled >= 5
    for task_id in ids:
        assert await status_of(session, task_id) is TaskStatus.CANCELLED
