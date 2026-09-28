"""Real concurrency: separate connections with real commits (no savepoint isolation).

Each test cleans up the rows it created.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.exceptions import BusinessRuleError
from app.domain.status import MAX_ACTIVE_TASKS_PER_ASSIGNEE, TaskStatus
from app.models import Comment, Task, User
from app.repositories.task import TaskRepository
from app.schemas.task import TaskUpdate
from app.services.task import TaskService


@pytest.fixture
async def factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    created: dict[str, list[int]] = {"users": [], "tasks": []}
    maker.created = created  # type: ignore[attr-defined]
    yield maker
    async with maker() as s:
        await s.execute(delete(Task).where(Task.id.in_(created["tasks"])))
        await s.execute(delete(User).where(User.id.in_(created["users"])))
        await s.commit()


async def make_user(factory: async_sessionmaker[AsyncSession], name: str) -> User:
    async with factory() as s:
        user = User(email=f"{name}-{id(s)}@example.com", full_name=name, hashed_password="x")
        s.add(user)
        await s.commit()
    factory.created["users"].append(user.id)  # type: ignore[attr-defined]
    return user


async def make_tasks(
    factory: async_sessionmaker[AsyncSession], author: User, count: int, **fields: Any
) -> list[int]:
    async with factory() as s:
        tasks = [Task(title=f"t{i}", author_id=author.id, **fields) for i in range(count)]
        s.add_all(tasks)
        await s.commit()
    ids = [t.id for t in tasks]
    factory.created["tasks"].extend(ids)  # type: ignore[attr-defined]
    return ids


async def test_parallel_assignments_cannot_exceed_limit(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    author, busy = await make_user(factory, "author"), await make_user(factory, "busy")
    await make_tasks(
        factory,
        author,
        MAX_ACTIVE_TASKS_PER_ASSIGNEE - 1,
        assignee_id=busy.id,
        status=TaskStatus.TODO,
    )
    first, second = await make_tasks(factory, author, 2, status=TaskStatus.TODO)

    # Widen the race window: without the user row lock both requests would count 9 and pass.
    original = TaskRepository.count_active_for_assignee

    async def slow_count(self: TaskRepository, *args: Any, **kwargs: Any) -> int:
        result = await original(self, *args, **kwargs)
        await asyncio.sleep(0.3)
        return result

    monkeypatch.setattr(TaskRepository, "count_active_for_assignee", slow_count)

    async def assign(task_id: int) -> str:
        async with factory() as s:
            try:
                await TaskService(s).update(task_id, TaskUpdate(assignee_id=busy.id), author)
            except BusinessRuleError as exc:
                return exc.code
            return "ok"

    results = await asyncio.gather(assign(first), assign(second))

    assert sorted(results) == ["ASSIGNEE_TASK_LIMIT_EXCEEDED", "ok"]
    async with factory() as s:
        active = await TaskRepository(s).count_active_for_assignee(busy.id)
    assert active == MAX_ACTIVE_TASKS_PER_ASSIGNEE


async def test_locked_task_does_not_block_new_comments(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Task row lock is NO KEY UPDATE: FK check of a comment insert (KEY SHARE) isn't blocked."""
    author = await make_user(factory, "author")
    (task_id,) = await make_tasks(factory, author, 1)

    async with factory() as editor, factory() as commenter:
        locked = await TaskRepository(editor).get(task_id, for_update=True)  # holds the lock
        assert locked is not None

        # Would hang (and hit the timeout) with plain FOR UPDATE.
        await commenter.execute(text("SET LOCAL lock_timeout = '1s'"))
        commenter.add(Comment(task_id=task_id, author_id=author.id, text="while editing"))
        await commenter.commit()

        await editor.rollback()

    async with factory() as s:
        texts = await s.scalars(select(Comment.text).where(Comment.task_id == task_id))
        assert list(texts) == ["while editing"]
