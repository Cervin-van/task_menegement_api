from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import time_machine
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.status import TaskPriority, TaskStatus
from app.models import Task
from tests.conftest import UserFactory

OVERDUE = "/api/v1/tasks/overdue"
STATS = "/api/v1/tasks/stats"


@pytest.fixture
async def user(create_user: UserFactory) -> dict[str, Any]:
    return await create_user()


async def seed(session: AsyncSession, author_id: int, *specs: dict[str, Any]) -> None:
    session.add_all(Task(**{"title": "Task", "author_id": author_id, **spec}) for spec in specs)
    await session.flush()


def ago(**kw: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


def ahead(**kw: float) -> datetime:
    return datetime.now(UTC) + timedelta(**kw)


# --- overdue ------------------------------------------------------------------


async def test_overdue_returns_only_unfinished_tasks_past_deadline(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "overdue-todo", "status": TaskStatus.TODO, "deadline": ago(days=1)},
        {"title": "overdue-review", "status": TaskStatus.REVIEW, "deadline": ago(hours=1)},
        {"title": "done-late", "status": TaskStatus.DONE, "deadline": ago(days=2)},
        {"title": "cancelled-late", "status": TaskStatus.CANCELLED, "deadline": ago(days=2)},
        {"title": "future", "status": TaskStatus.TODO, "deadline": ahead(days=1)},
        {"title": "no-deadline", "status": TaskStatus.TODO},
    )

    resp = await client.get(OVERDUE, headers=user["headers"])

    assert resp.status_code == 200
    body = resp.json()
    # Most overdue first.
    assert [t["title"] for t in body["items"]] == ["overdue-todo", "overdue-review"]
    assert body["total"] == 2


async def test_overdue_pagination(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        *({"title": f"late-{i}", "deadline": ago(days=10 - i)} for i in range(3)),
    )

    resp = await client.get(OVERDUE, params={"size": 2, "page": 2}, headers=user["headers"])

    body = resp.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert [t["title"] for t in body["items"]] == ["late-2"]


async def test_task_becomes_overdue_when_deadline_passes(
    client: AsyncClient, user: dict[str, Any]
) -> None:
    deadline = ahead(minutes=5)
    created = await client.post(
        "/api/v1/tasks",
        json={"title": "soon", "deadline": deadline.isoformat()},
        headers=user["headers"],
    )
    assert created.status_code == 201

    assert (await client.get(OVERDUE, headers=user["headers"])).json()["total"] == 0
    with time_machine.travel(deadline + timedelta(minutes=1)):
        resp = await client.get(OVERDUE, headers=user["headers"])
    assert [t["title"] for t in resp.json()["items"]] == ["soon"]


# --- stats --------------------------------------------------------------------


async def test_stats_on_empty_db_returns_all_keys_with_zeros(
    client: AsyncClient, user: dict[str, Any]
) -> None:
    resp = await client.get(STATS, headers=user["headers"])

    assert resp.status_code == 200
    assert resp.json() == {
        "total": 0,
        "by_status": {s.value: 0 for s in TaskStatus},
        "by_priority": {p.value: 0 for p in TaskPriority},
        "overdue": 0,
        "active": 0,
    }


async def test_stats_counts(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"status": TaskStatus.BACKLOG, "priority": TaskPriority.LOW},
        {"status": TaskStatus.TODO, "priority": TaskPriority.HIGH, "deadline": ago(days=1)},
        {"status": TaskStatus.IN_PROGRESS, "priority": TaskPriority.HIGH},
        {"status": TaskStatus.REVIEW, "priority": TaskPriority.MEDIUM, "deadline": ahead(days=1)},
        {"status": TaskStatus.DONE, "priority": TaskPriority.MEDIUM, "deadline": ago(days=3)},
        {"status": TaskStatus.CANCELLED, "priority": TaskPriority.LOW, "deadline": ago(days=3)},
        {"status": TaskStatus.BACKLOG, "priority": TaskPriority.HIGH, "deadline": ago(hours=2)},
    )

    resp = await client.get(STATS, headers=user["headers"])

    assert resp.json() == {
        "total": 7,
        "by_status": {
            "backlog": 2,
            "todo": 1,
            "in_progress": 1,
            "review": 1,
            "done": 1,
            "cancelled": 1,
        },
        "by_priority": {"low": 2, "medium": 2, "high": 3},
        # overdue: todo(ago) + backlog(ago); done/cancelled with past deadline don't count.
        "overdue": 2,
        # active: todo + in_progress + review.
        "active": 3,
    }


async def test_stats_requires_auth(client: AsyncClient) -> None:
    assert (await client.get(STATS)).status_code == 401
    assert (await client.get(OVERDUE)).status_code == 401
