import itertools
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import time_machine
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.status import (
    FINAL_STATUSES,
    MAX_ACTIVE_TASKS_PER_ASSIGNEE,
    TaskStatus,
    can_transition,
)
from app.models import Task
from tests.conftest import TaskFactory, UserFactory, future

SetStatus = Callable[[int, TaskStatus], Awaitable[None]]


def status_url(task_id: int) -> str:
    return f"/api/v1/tasks/{task_id}/status"


async def move(client: AsyncClient, task_id: int, status: str, user: dict[str, Any]) -> Any:
    return await client.patch(status_url(task_id), json={"status": status}, headers=user["headers"])


async def test_full_lifecycle_to_done(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, assignee = await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"], deadline=future())

    for status in ("todo", "in_progress", "review", "done"):
        resp = await move(client, task["id"], status, assignee)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == status


@pytest.mark.parametrize(
    ("current", "target"),
    list(itertools.product(TaskStatus, TaskStatus)),
    ids=lambda s: s.value,
)
async def test_transition_matrix(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    set_status: SetStatus,
    current: TaskStatus,
    target: TaskStatus,
) -> None:
    """Every (from, to) pair: only ALLOWED_TRANSITIONS succeed, rollbacks/skips fail."""
    author, assignee = await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"], deadline=future())
    await set_status(task["id"], current)

    resp = await move(client, task["id"], target, author)

    if current in FINAL_STATUSES:
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "TASK_NOT_EDITABLE"
    elif can_transition(current, target):
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == target
    else:
        assert resp.status_code == 409
        error = resp.json()["error"]
        assert error["code"] == "INVALID_STATUS_TRANSITION"
        assert error["details"]["from"] == current
        assert error["details"]["to"] == target


async def test_done_requires_assignee(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory, set_status: SetStatus
) -> None:
    author = await create_user()
    task = await create_task(author)
    await set_status(task["id"], TaskStatus.REVIEW)

    resp = await move(client, task["id"], "done", author)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DONE_REQUIRES_ASSIGNEE"


async def test_done_after_deadline_fails(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory, set_status: SetStatus
) -> None:
    author, assignee = await create_user(), await create_user()
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    task = await create_task(author, assignee_id=assignee["id"], deadline=deadline.isoformat())
    await set_status(task["id"], TaskStatus.REVIEW)

    # Jump past the deadline (token TTL is 30 min, so auth still works).
    with time_machine.travel(deadline + timedelta(minutes=5)):
        resp = await move(client, task["id"], "done", assignee)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DONE_AFTER_DEADLINE"


async def test_stranger_cannot_change_status(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, stranger = await create_user(), await create_user()
    task = await create_task(author)

    resp = await move(client, task["id"], "todo", stranger)

    assert resp.status_code == 403


async def test_invalid_status_value_is_rejected(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author)

    resp = await move(client, task["id"], "archived", author)

    assert resp.status_code == 422


async def test_moving_to_todo_respects_assignee_limit(
    client: AsyncClient,
    session: AsyncSession,
    create_user: UserFactory,
    create_task: TaskFactory,
) -> None:
    author, busy = await create_user(), await create_user()
    session.add_all(
        Task(title=f"t{i}", author_id=author["id"], assignee_id=busy["id"], status=status)
        for i, status in zip(
            range(MAX_ACTIVE_TASKS_PER_ASSIGNEE),
            itertools.cycle([TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW]),
            strict=False,
        )
    )
    await session.flush()
    task = await create_task(author, assignee_id=busy["id"])  # backlog: allowed

    resp = await move(client, task["id"], "todo", author)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ASSIGNEE_TASK_LIMIT_EXCEEDED"


async def test_done_and_cancelled_tasks_do_not_count_towards_limit(
    client: AsyncClient,
    session: AsyncSession,
    create_user: UserFactory,
    create_task: TaskFactory,
) -> None:
    author, assignee = await create_user(), await create_user()
    session.add_all(
        Task(title=f"t{i}", author_id=author["id"], assignee_id=assignee["id"], status=status)
        for i, status in enumerate([TaskStatus.DONE, TaskStatus.CANCELLED] * 10)
    )
    await session.flush()
    task = await create_task(author, assignee_id=assignee["id"])

    resp = await move(client, task["id"], "todo", author)

    assert resp.status_code == 200


async def test_status_change_notifies_other_participants(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    author, assignee = await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"])
    caplog.set_level(logging.INFO, logger="app.notifications")

    resp = await move(client, task["id"], "todo", assignee)

    assert resp.status_code == 200
    messages = [r.getMessage() for r in caplog.records if r.name == "app.notifications"]
    assert any(
        "task.status_changed" in m and author["email"] in m and "backlog->todo" in m
        for m in messages
    )
    # The actor is not notified about their own change.
    assert not any(assignee["email"] in m for m in messages)


async def test_assignment_notifies_assignee(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    author, assignee = await create_user(), await create_user()
    task = await create_task(author)
    caplog.set_level(logging.INFO, logger="app.notifications")

    resp = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"assignee_id": assignee["id"]},
        headers=author["headers"],
    )

    assert resp.status_code == 200
    assert any(
        "task.assigned" in r.getMessage() and assignee["email"] in r.getMessage()
        for r in caplog.records
    )
