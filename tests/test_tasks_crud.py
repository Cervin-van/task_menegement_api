from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.status import MAX_ACTIVE_TASKS_PER_ASSIGNEE, TaskStatus
from app.models import Task
from tests.conftest import TaskFactory, UserFactory, future

TASKS = "/api/v1/tasks"
SetStatus = Callable[[int, TaskStatus], Awaitable[None]]


# --- create -------------------------------------------------------------------


async def test_create_task_defaults(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()

    task = await create_task(author, title="Write tests", deadline=future())

    assert task["status"] == "backlog"
    assert task["priority"] == "medium"
    assert task["author"]["id"] == author["id"]
    assert task["assignee"] is None
    assert task["created_at"] and task["updated_at"]


async def test_create_task_with_assignee(
    create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, assignee = await create_user(), await create_user()

    task = await create_task(author, assignee_id=assignee["id"], priority="high")

    assert task["assignee"]["id"] == assignee["id"]
    assert task["priority"] == "high"


async def test_create_task_with_past_deadline_fails(
    client: AsyncClient, create_user: UserFactory
) -> None:
    author = await create_user()
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    resp = await client.post(
        TASKS, json={"title": "T", "deadline": past}, headers=author["headers"]
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "DEADLINE_IN_PAST"


async def test_create_task_with_naive_deadline_fails(
    client: AsyncClient, create_user: UserFactory
) -> None:
    author = await create_user()

    resp = await client.post(
        TASKS, json={"title": "T", "deadline": "2099-01-01T10:00:00"}, headers=author["headers"]
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_create_task_with_unknown_assignee_fails(
    client: AsyncClient, create_user: UserFactory
) -> None:
    author = await create_user()

    resp = await client.post(
        TASKS, json={"title": "T", "assignee_id": 999_999}, headers=author["headers"]
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ASSIGNEE_NOT_FOUND"


async def test_create_task_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(TASKS, json={"title": "T"})

    assert resp.status_code == 401


# --- read ---------------------------------------------------------------------


async def test_get_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, other = await create_user(), await create_user()
    task = await create_task(author)

    resp = await client.get(f"{TASKS}/{task['id']}", headers=other["headers"])

    assert resp.status_code == 200
    assert resp.json()["id"] == task["id"]


async def test_get_missing_task_returns_404(client: AsyncClient, create_user: UserFactory) -> None:
    user = await create_user()

    resp = await client.get(f"{TASKS}/999999", headers=user["headers"])

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"


# --- update -------------------------------------------------------------------


async def test_author_can_update_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author, description="old")

    resp = await client.patch(
        f"{TASKS}/{task['id']}",
        json={"title": "New title", "priority": "low", "description": None},
        headers=author["headers"],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "New title"
    assert body["priority"] == "low"
    assert body["description"] is None
    assert body["updated_at"] >= task["updated_at"]


async def test_assignee_can_update_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, assignee = await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"])

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"description": "done by me"}, headers=assignee["headers"]
    )

    assert resp.status_code == 200


async def test_stranger_cannot_update_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, stranger = await create_user(), await create_user()
    task = await create_task(author)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"title": "hack"}, headers=stranger["headers"]
    )

    assert resp.status_code == 403


async def test_update_cannot_change_status(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"status": "done"}, headers=author["headers"]
    )

    assert resp.status_code == 422


async def test_update_title_to_null_fails(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"title": None}, headers=author["headers"]
    )

    assert resp.status_code == 422


@pytest.mark.parametrize("status", [TaskStatus.DONE, TaskStatus.CANCELLED])
async def test_final_task_is_not_editable(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    set_status: SetStatus,
    status: TaskStatus,
) -> None:
    author = await create_user()
    task = await create_task(author)
    await set_status(task["id"], status)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"title": "x"}, headers=author["headers"]
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_NOT_EDITABLE"


async def test_assignee_locked_in_review(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    set_status: SetStatus,
) -> None:
    author, assignee, other = await create_user(), await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"])
    await set_status(task["id"], TaskStatus.REVIEW)
    url = f"{TASKS}/{task['id']}"

    resp = await client.patch(url, json={"assignee_id": other["id"]}, headers=author["headers"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ASSIGNEE_LOCKED"

    # Other fields are still editable in review.
    resp = await client.patch(url, json={"title": "Polished"}, headers=author["headers"])
    assert resp.status_code == 200


async def test_update_deadline_to_past_fails(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"deadline": past}, headers=author["headers"]
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "DEADLINE_IN_PAST"


async def test_assign_active_task_over_limit_fails(
    client: AsyncClient,
    session: AsyncSession,
    create_user: UserFactory,
    create_task: TaskFactory,
    set_status: SetStatus,
) -> None:
    author, busy = await create_user(), await create_user()
    session.add_all(
        Task(title=f"t{i}", author_id=author["id"], assignee_id=busy["id"], status=TaskStatus.TODO)
        for i in range(MAX_ACTIVE_TASKS_PER_ASSIGNEE)
    )
    await session.flush()
    task = await create_task(author)
    await set_status(task["id"], TaskStatus.TODO)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"assignee_id": busy["id"]}, headers=author["headers"]
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "ASSIGNEE_TASK_LIMIT_EXCEEDED"


async def test_assign_backlog_task_ignores_limit(
    client: AsyncClient,
    session: AsyncSession,
    create_user: UserFactory,
    create_task: TaskFactory,
) -> None:
    author, busy = await create_user(), await create_user()
    session.add_all(
        Task(title=f"t{i}", author_id=author["id"], assignee_id=busy["id"], status=TaskStatus.TODO)
        for i in range(MAX_ACTIVE_TASKS_PER_ASSIGNEE)
    )
    await session.flush()
    task = await create_task(author)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"assignee_id": busy["id"]}, headers=author["headers"]
    )

    assert resp.status_code == 200


# --- delete -------------------------------------------------------------------


async def test_author_can_delete_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author)
    url = f"{TASKS}/{task['id']}"

    resp = await client.delete(url, headers=author["headers"])
    assert resp.status_code == 204

    resp = await client.get(url, headers=author["headers"])
    assert resp.status_code == 404


async def test_assignee_cannot_delete_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, assignee = await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"])

    resp = await client.delete(f"{TASKS}/{task['id']}", headers=assignee["headers"])

    assert resp.status_code == 403


@pytest.mark.parametrize("status", [TaskStatus.IN_PROGRESS, TaskStatus.REVIEW])
async def test_cannot_delete_task_in_progress_or_review(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    set_status: SetStatus,
    status: TaskStatus,
) -> None:
    author = await create_user()
    task = await create_task(author)
    await set_status(task["id"], status)

    resp = await client.delete(f"{TASKS}/{task['id']}", headers=author["headers"])

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_NOT_DELETABLE"


@pytest.mark.parametrize("status", [TaskStatus.DONE, TaskStatus.CANCELLED])
async def test_can_delete_finished_task(
    client: AsyncClient,
    create_user: UserFactory,
    create_task: TaskFactory,
    set_status: SetStatus,
    status: TaskStatus,
) -> None:
    author = await create_user()
    task = await create_task(author)
    await set_status(task["id"], status)

    resp = await client.delete(f"{TASKS}/{task['id']}", headers=author["headers"])

    assert resp.status_code == 204
