from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.status import TaskPriority, TaskStatus
from app.models import Task
from tests.conftest import UserFactory

TASKS = "/api/v1/tasks"
NOW = datetime.now(UTC)


@pytest.fixture
async def user(create_user: UserFactory) -> dict[str, Any]:
    return await create_user()


async def seed(session: AsyncSession, author_id: int, *specs: dict[str, Any]) -> list[Task]:
    """Inserts tasks directly (bypasses API rules) for list/sort scenarios."""
    tasks = [Task(**{"title": "Task", "author_id": author_id, **spec}) for spec in specs]
    session.add_all(tasks)
    await session.flush()
    return tasks


async def list_titles(client: AsyncClient, user: dict[str, Any], **params: Any) -> list[str]:
    resp = await client.get(TASKS, params=params, headers=user["headers"])
    assert resp.status_code == 200, resp.text
    return [t["title"] for t in resp.json()["items"]]


# --- search -------------------------------------------------------------------


async def test_search_by_title_and_description_case_insensitive(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "Fix LOGIN bug"},
        {"title": "Other", "description": "users cannot login on mobile"},
        {"title": "Unrelated", "description": "nothing here"},
    )

    titles = await list_titles(client, user, search="login")

    assert sorted(titles) == ["Fix LOGIN bug", "Other"]


async def test_search_escapes_like_wildcards(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(session, user["id"], {"title": "Coverage 100%"}, {"title": "Coverage 90"})

    assert await list_titles(client, user, search="%") == ["Coverage 100%"]
    assert await list_titles(client, user, search="_") == []


# --- filters ------------------------------------------------------------------


async def test_filter_by_multiple_statuses(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "b", "status": TaskStatus.BACKLOG},
        {"title": "t", "status": TaskStatus.TODO},
        {"title": "d", "status": TaskStatus.DONE},
    )

    resp = await client.get(
        TASKS, params=[("status", "todo"), ("status", "done")], headers=user["headers"]
    )

    assert resp.status_code == 200
    assert sorted(t["title"] for t in resp.json()["items"]) == ["d", "t"]


async def test_filter_by_priority(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "hi", "priority": TaskPriority.HIGH},
        {"title": "lo", "priority": TaskPriority.LOW},
    )

    assert await list_titles(client, user, priority="high") == ["hi"]


async def test_filter_by_assignee(
    client: AsyncClient, session: AsyncSession, create_user: UserFactory, user: dict[str, Any]
) -> None:
    other = await create_user()
    await seed(
        session, user["id"], {"title": "mine", "assignee_id": other["id"]}, {"title": "free"}
    )

    assert await list_titles(client, user, assignee_id=other["id"]) == ["mine"]


async def test_filter_by_deadline_range(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "d1", "deadline": NOW + timedelta(days=1)},
        {"title": "d5", "deadline": NOW + timedelta(days=5)},
        {"title": "d10", "deadline": NOW + timedelta(days=10)},
        {"title": "none"},
    )

    titles = await list_titles(
        client,
        user,
        deadline_from=(NOW + timedelta(days=2)).isoformat(),
        deadline_to=(NOW + timedelta(days=7)).isoformat(),
    )

    assert titles == ["d5"]


async def test_invalid_deadline_range_returns_422(
    client: AsyncClient, user: dict[str, Any]
) -> None:
    resp = await client.get(
        TASKS,
        params={
            "deadline_from": (NOW + timedelta(days=5)).isoformat(),
            "deadline_to": NOW.isoformat(),
        },
        headers=user["headers"],
    )

    assert resp.status_code == 422


async def test_unknown_query_param_returns_422(client: AsyncClient, user: dict[str, Any]) -> None:
    resp = await client.get(TASKS, params={"sort": "title"}, headers=user["headers"])

    assert resp.status_code == 422


# --- sorting ------------------------------------------------------------------


async def test_default_sort_priority_then_nearest_deadline(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "low-soon", "priority": TaskPriority.LOW, "deadline": NOW + timedelta(days=1)},
        {"title": "high-none", "priority": TaskPriority.HIGH},
        {"title": "high-late", "priority": TaskPriority.HIGH, "deadline": NOW + timedelta(days=9)},
        {"title": "high-soon", "priority": TaskPriority.HIGH, "deadline": NOW + timedelta(days=2)},
        {"title": "medium", "priority": TaskPriority.MEDIUM, "deadline": NOW + timedelta(days=3)},
    )

    assert await list_titles(client, user) == [
        "high-soon",
        "high-late",
        "high-none",
        "medium",
        "low-soon",
    ]


async def test_sort_by_created_at(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "second", "created_at": NOW - timedelta(days=2)},
        {"title": "first", "created_at": NOW - timedelta(days=3)},
        {"title": "third", "created_at": NOW - timedelta(days=1)},
    )

    assert await list_titles(client, user, sort_by="created_at") == ["third", "second", "first"]
    assert await list_titles(client, user, sort_by="created_at", order="asc") == [
        "first",
        "second",
        "third",
    ]


@pytest.mark.parametrize(
    ("order", "expected"),
    [("asc", ["soon", "late", "none"]), ("desc", ["late", "soon", "none"])],
)
async def test_sort_by_deadline_nulls_last(
    client: AsyncClient,
    session: AsyncSession,
    user: dict[str, Any],
    order: str,
    expected: list[str],
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "none"},
        {"title": "late", "deadline": NOW + timedelta(days=9)},
        {"title": "soon", "deadline": NOW + timedelta(days=1)},
    )

    assert await list_titles(client, user, sort_by="deadline", order=order) == expected


async def test_sort_by_priority_asc(
    client: AsyncClient, session: AsyncSession, user: dict[str, Any]
) -> None:
    await seed(
        session,
        user["id"],
        {"title": "h", "priority": TaskPriority.HIGH},
        {"title": "l", "priority": TaskPriority.LOW},
        {"title": "m", "priority": TaskPriority.MEDIUM},
    )

    assert await list_titles(client, user, sort_by="priority", order="asc") == ["l", "m", "h"]


# --- pagination ---------------------------------------------------------------


async def test_pagination(client: AsyncClient, session: AsyncSession, user: dict[str, Any]) -> None:
    await seed(
        session,
        user["id"],
        *({"title": f"t{i:02}", "created_at": NOW - timedelta(minutes=i)} for i in range(5)),
    )

    resp = await client.get(
        TASKS,
        params={"sort_by": "created_at", "page": 2, "size": 2},
        headers=user["headers"],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["pages"] == 3
    assert body["page"] == 2
    assert body["size"] == 2
    assert [t["title"] for t in body["items"]] == ["t02", "t03"]


async def test_empty_result_page(client: AsyncClient, user: dict[str, Any]) -> None:
    resp = await client.get(TASKS, params={"search": "no-such-task"}, headers=user["headers"])

    assert resp.json() == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}


@pytest.mark.parametrize("params", [{"size": 101}, {"size": 0}, {"page": 0}])
async def test_invalid_pagination_returns_422(
    client: AsyncClient, user: dict[str, Any], params: dict[str, int]
) -> None:
    resp = await client.get(TASKS, params=params, headers=user["headers"])

    assert resp.status_code == 422


async def test_order_without_sort_by_returns_422(client: AsyncClient, user: dict[str, Any]) -> None:
    resp = await client.get(TASKS, params={"order": "desc"}, headers=user["headers"])

    assert resp.status_code == 422
