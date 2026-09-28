from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.status import ACTIVE_STATUSES
from app.models import Task


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, task_id: int, *, for_update: bool = False) -> Task | None:
        stmt = (
            select(Task)
            .where(Task.id == task_id)
            .options(selectinload(Task.author), selectinload(Task.assignee))
            # Refresh identity-map copy: server-side updated_at, reloaded relations.
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update(of=Task)
        return await self.session.scalar(stmt)

    async def add(self, task: Task) -> Task:
        self.session.add(task)
        await self.session.flush()
        return task

    async def delete(self, task: Task) -> None:
        await self.session.delete(task)
        await self.session.flush()

    async def count_active_for_assignee(
        self, user_id: int, *, exclude_task_id: int | None = None
    ) -> int:
        stmt = select(func.count(Task.id)).where(
            Task.assignee_id == user_id, Task.status.in_(ACTIVE_STATUSES)
        )
        if exclude_task_id is not None:
            stmt = stmt.where(Task.id != exclude_task_id)
        return await self.session.scalar(stmt) or 0
