from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.status import TaskStatus
from app.models import Comment
from tests.conftest import TaskFactory, UserFactory

SetStatus = Callable[[int, TaskStatus], Awaitable[None]]


def comments_url(task_id: int) -> str:
    return f"/api/v1/tasks/{task_id}/comments"


async def test_add_comment(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author = await create_user()
    task = await create_task(author)

    resp = await client.post(
        comments_url(task["id"]), json={"text": "  Looks good  "}, headers=author["headers"]
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["text"] == "Looks good"
    assert body["task_id"] == task["id"]
    assert body["author"]["id"] == author["id"]
    assert body["created_at"]


async def test_any_user_can_comment(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, stranger = await create_user(), await create_user()
    task = await create_task(author)

    resp = await client.post(
        comments_url(task["id"]), json={"text": "Question"}, headers=stranger["headers"]
    )

    assert resp.status_code == 201


async def test_can_comment_on_done_task(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory, set_status: SetStatus
) -> None:
    author = await create_user()
    task = await create_task(author)
    await set_status(task["id"], TaskStatus.DONE)

    resp = await client.post(
        comments_url(task["id"]), json={"text": "Retro note"}, headers=author["headers"]
    )

    assert resp.status_code == 201


@pytest.mark.parametrize("text", ["", "   ", "x" * 5001])
async def test_invalid_comment_text_returns_422(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory, text: str
) -> None:
    author = await create_user()
    task = await create_task(author)

    resp = await client.post(
        comments_url(task["id"]), json={"text": text}, headers=author["headers"]
    )

    assert resp.status_code == 422


async def test_comments_on_missing_task_return_404(
    client: AsyncClient, create_user: UserFactory
) -> None:
    user = await create_user()

    post = await client.post(comments_url(999_999), json={"text": "hi"}, headers=user["headers"])
    get = await client.get(comments_url(999_999), headers=user["headers"])

    assert post.status_code == get.status_code == 404
    assert post.json()["error"]["code"] == "TASK_NOT_FOUND"


async def test_comments_require_auth(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    task = await create_task(await create_user())

    assert (await client.get(comments_url(task["id"]))).status_code == 401
    assert (await client.post(comments_url(task["id"]), json={"text": "x"})).status_code == 401


async def test_list_comments_chronological_with_pagination(
    client: AsyncClient, create_user: UserFactory, create_task: TaskFactory
) -> None:
    author, other = await create_user(), await create_user()
    task = await create_task(author)
    other_task = await create_task(author)
    for i, user in enumerate([author, other, author]):
        await client.post(comments_url(task["id"]), json={"text": f"c{i}"}, headers=user["headers"])
    await client.post(
        comments_url(other_task["id"]), json={"text": "elsewhere"}, headers=author["headers"]
    )

    first = await client.get(comments_url(task["id"]), params={"size": 2}, headers=other["headers"])
    second = await client.get(
        comments_url(task["id"]), params={"size": 2, "page": 2}, headers=other["headers"]
    )

    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 3
    assert body["pages"] == 2
    assert [c["text"] for c in body["items"]] == ["c0", "c1"]
    assert [c["text"] for c in second.json()["items"]] == ["c2"]


async def test_deleting_task_deletes_its_comments(
    client: AsyncClient,
    session: AsyncSession,
    create_user: UserFactory,
    create_task: TaskFactory,
) -> None:
    author = await create_user()
    task = await create_task(author)
    await client.post(comments_url(task["id"]), json={"text": "bye"}, headers=author["headers"])

    resp = await client.delete(f"/api/v1/tasks/{task['id']}", headers=author["headers"])

    assert resp.status_code == 204
    count = await session.scalar(
        select(func.count()).select_from(Comment).where(Comment.task_id == task["id"])
    )
    assert count == 0
