from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, RowMapping, UnaryExpression, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.status import ACTIVE_STATUSES, FINAL_STATUSES, TaskPriority, TaskStatus
from app.models import Task
from app.schemas.common import PageParams
from app.schemas.task import SortOrder, TaskListQuery, TaskSortField

_SORT_COLUMNS = {
    TaskSortField.CREATED_AT: Task.created_at,
    TaskSortField.DEADLINE: Task.deadline,
    TaskSortField.PRIORITY: Task.priority,
}
_DEFAULT_SORT_ORDER = {
    TaskSortField.CREATED_AT: SortOrder.DESC,  # newest first
    TaskSortField.DEADLINE: SortOrder.ASC,  # nearest first
    TaskSortField.PRIORITY: SortOrder.DESC,  # high first
}


def overdue_condition(now: datetime) -> ColumnElement[bool]:
    """Single definition of "overdue": used by the endpoint, stats and the auto-cancel worker.

    `now` comes from the app clock (not DB now()) to stay consistent with service checks.
    """
    return and_(Task.deadline < now, Task.status.not_in(FINAL_STATUSES))


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
            # NO KEY UPDATE: serializes edits of this task, but (unlike FOR UPDATE) doesn't
            # block FK checks (KEY SHARE) of inserts referencing it, e.g. new comments.
            stmt = stmt.with_for_update(of=Task, key_share=True)
        return await self.session.scalar(stmt)

    async def exists(self, task_id: int) -> bool:
        return bool(
            await self.session.scalar(select(select(Task.id).where(Task.id == task_id).exists()))
        )

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

    async def list_tasks(self, query: TaskListQuery) -> tuple[Sequence[Task], int]:
        conditions = self._filters(query)

        total = await self.session.scalar(select(func.count()).select_from(Task).where(*conditions))
        items = await self.session.scalars(
            select(Task)
            .where(*conditions)
            .options(selectinload(Task.author), selectinload(Task.assignee))
            .order_by(*self._ordering(query))
            .offset(query.offset)
            .limit(query.size)
        )
        return items.all(), total or 0

    @staticmethod
    def _filters(query: TaskListQuery) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if query.search:
            # Escape LIKE wildcards: user input "%" must match a literal percent sign.
            escaped = query.search.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            conditions.append(
                or_(
                    Task.title.ilike(pattern, escape="\\"),
                    Task.description.ilike(pattern, escape="\\"),
                )
            )
        if query.status:
            conditions.append(Task.status.in_(query.status))
        if query.priority:
            conditions.append(Task.priority.in_(query.priority))
        if query.assignee_id is not None:
            conditions.append(Task.assignee_id == query.assignee_id)
        if query.deadline_from is not None:
            conditions.append(Task.deadline >= query.deadline_from)
        if query.deadline_to is not None:
            conditions.append(Task.deadline <= query.deadline_to)
        return conditions

    @staticmethod
    def _ordering(query: TaskListQuery) -> list[UnaryExpression[object]]:
        if query.sort_by is None:
            # Matches ix_tasks_priority_deadline.
            return [Task.priority.desc(), Task.deadline.asc().nulls_last(), Task.id.asc()]

        column = _SORT_COLUMNS[query.sort_by]
        order = query.order or _DEFAULT_SORT_ORDER[query.sort_by]
        primary = column.asc() if order is SortOrder.ASC else column.desc()
        # Tasks without deadline go last in both directions; id makes pagination stable.
        return [primary.nulls_last(), Task.id.asc()]

    async def list_overdue(self, now: datetime, params: PageParams) -> tuple[Sequence[Task], int]:
        condition = overdue_condition(now)
        total = await self.session.scalar(select(func.count()).select_from(Task).where(condition))
        items = await self.session.scalars(
            select(Task)
            .where(condition)
            .options(selectinload(Task.author), selectinload(Task.assignee))
            .order_by(Task.deadline.asc(), Task.id.asc())  # most overdue first
            .offset(params.offset)
            .limit(params.size)
        )
        return items.all(), total or 0

    async def stats(self, now: datetime) -> RowMapping:
        """All counters in ONE table scan: count(*) FILTER (WHERE ...) per metric.

        Every status/priority gets its own column, so missing values come back as 0.
        """
        columns = [
            func.count().label("total"),
            func.count().filter(overdue_condition(now)).label("overdue"),
            func.count().filter(Task.status.in_(ACTIVE_STATUSES)).label("active"),
            *(func.count().filter(Task.status == s).label(f"status_{s}") for s in TaskStatus),
            *(func.count().filter(Task.priority == p).label(f"priority_{p}") for p in TaskPriority),
        ]
        result = await self.session.execute(select(*columns).select_from(Task))
        return result.mappings().one()
