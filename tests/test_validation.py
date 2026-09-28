from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import time_machine
from httpx import AsyncClient

from tests.conftest import TaskFactory, UserFactory, future

TASKS = "/api/v1/tasks"


@pytest.fixture
async def user(create_user: UserFactory) -> dict[str, Any]:
    return await create_user()


@pytest.mark.parametrize(
    "body",
    [
        {"title": "a\u0000b"},
        {"title": "ok", "description": "x\u0000"},
        {"title": "ok", "description": "x" * 10_001},
        {"title": "ok", "deadline": "9999-12-31T23:59:59-14:00"},
        {"title": "ok", "deadline": 1_900_000_000},
        {"title": "ok", "assignee_id": True},
        {"title": "ok", "assignee_id": "1"},
        {"title": "ok", "assignee_id": 1.0},
    ],
)
async def test_invalid_task_body_returns_422(
    client: AsyncClient, user: dict[str, Any], body: dict[str, Any]
) -> None:
    resp = await client.post(TASKS, json=body, headers=user["headers"])

    assert resp.status_code == 422, resp.text


async def test_blank_description_is_stored_as_null(
    client: AsyncClient, user: dict[str, Any]
) -> None:
    resp = await client.post(
        TASKS, json={"title": "ok", "description": "   "}, headers=user["headers"]
    )

    assert resp.status_code == 201
    assert resp.json()["description"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"page": 1_000_000_000_000_000_000},
        {"page": 21_474_837},
        {"search": "a\x00b"},
        {"deadline_from": "0001-01-01T00:00:00+14:00"},
        {"assignee_id": 99_999_999_999},
    ],
)
async def test_invalid_list_params_return_422(
    client: AsyncClient, user: dict[str, Any], params: dict[str, Any]
) -> None:
    resp = await client.get(TASKS, params=params, headers=user["headers"])

    assert resp.status_code == 422, resp.text


async def test_list_accepts_numeric_query_id(client: AsyncClient, user: dict[str, Any]) -> None:
    # Query strings are always text: lax parsing must keep working there.
    resp = await client.get(TASKS, params={"assignee_id": "1"}, headers=user["headers"])

    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/v1/tasks/overdue?page=99999999999", None),
        ("GET", "/api/v1/tasks/overdue?status=todo", None),
        (
            "POST",
            "/api/v1/auth/register",
            {
                "email": "x@example.com",
                "password": "Str0ngPass!",
                "full_name": "X",
                "is_admin": True,
            },
        ),
        ("POST", "/api/v1/auth/refresh", {"refresh_token": "t", "extra": 1}),
    ],
)
async def test_unknown_fields_and_params_rejected(
    client: AsyncClient,
    user: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    resp = await client.request(method, path, json=body, headers=user["headers"])

    assert resp.status_code == 422, resp.text


async def test_status_and_comment_bodies_reject_unknown_fields(
    client: AsyncClient, user: dict[str, Any], create_task: TaskFactory
) -> None:
    task = await create_task(user)

    status = await client.patch(
        f"{TASKS}/{task['id']}/status",
        json={"status": "todo", "x": 1},
        headers=user["headers"],
    )
    comment = await client.post(
        f"{TASKS}/{task['id']}/comments",
        json={"text": "ok", "task_id": 999},
        headers=user["headers"],
    )
    nul_comment = await client.post(
        f"{TASKS}/{task['id']}/comments", json={"text": "a\u0000b"}, headers=user["headers"]
    )
    big_task = await client.post(
        f"{TASKS}/99999999999/comments", json={"text": "ok"}, headers=user["headers"]
    )

    assert status.status_code == comment.status_code == nul_comment.status_code == 422
    assert big_task.status_code == 422


async def test_patch_assignee_out_of_range_returns_422(
    client: AsyncClient, user: dict[str, Any], create_task: TaskFactory
) -> None:
    task = await create_task(user)

    resp = await client.patch(
        f"{TASKS}/{task['id']}", json={"assignee_id": 99_999_999_999}, headers=user["headers"]
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------- deadline ownership


async def test_only_author_can_change_deadline(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, assignee = await create_user(), await create_user()
    task = await create_task(author, assignee_id=assignee["id"], deadline=future(1))
    url = f"{TASKS}/{task['id']}"

    resp = await client.patch(url, json={"deadline": future(5)}, headers=assignee["headers"])
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "DEADLINE_CHANGE_FORBIDDEN"

    resp = await client.patch(url, json={"deadline": future(5)}, headers=author["headers"])
    assert resp.status_code == 200


async def test_overdue_deadline_cannot_be_removed(
    client: AsyncClient, user: dict[str, Any], create_task: TaskFactory
) -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    task = await create_task(user, deadline=deadline.isoformat())
    url = f"{TASKS}/{task['id']}"

    with time_machine.travel(deadline + timedelta(minutes=1)):
        removed = await client.patch(url, json={"deadline": None}, headers=user["headers"])
        moved = await client.patch(url, json={"deadline": future(3)}, headers=user["headers"])

    assert removed.status_code == 409
    assert removed.json()["error"]["code"] == "OVERDUE_DEADLINE_REMOVAL_FORBIDDEN"
    assert moved.status_code == 200
