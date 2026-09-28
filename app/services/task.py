from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domain.status import (
    ACTIVE_STATUSES,
    ASSIGNEE_LOCKED_STATUSES,
    FINAL_STATUSES,
    MAX_ACTIVE_TASKS_PER_ASSIGNEE,
    UNDELETABLE_STATUSES,
    TaskStatus,
)
from app.models import Task, User
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.schemas.task import TaskCreate, TaskUpdate


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.users = UserRepository(session)

    async def create(self, data: TaskCreate, author: User) -> Task:
        self._ensure_deadline_not_past(data.deadline)
        if data.assignee_id is not None:
            # New task is always backlog (not active) -> only existence check, no limit.
            await self._lock_assignee(data.assignee_id)

        task = Task(
            **data.model_dump(),
            author_id=author.id,
            status=TaskStatus.BACKLOG,
        )
        await self.tasks.add(task)
        await self.session.commit()
        return await self.get(task.id)

    async def get(self, task_id: int) -> Task:
        task = await self.tasks.get(task_id)
        if task is None:
            raise self._not_found(task_id)
        return task

    async def update(self, task_id: int, data: TaskUpdate, user: User) -> Task:
        task = await self._get_for_update(task_id)
        self._ensure_can_edit(task, user)
        if task.status in FINAL_STATUSES:
            raise BusinessRuleError(
                f"Task in status '{task.status}' cannot be edited",
                code="TASK_NOT_EDITABLE",
                details={"status": task.status},
            )

        changes = data.model_dump(exclude_unset=True)

        if "assignee_id" in changes and changes["assignee_id"] != task.assignee_id:
            if task.status in ASSIGNEE_LOCKED_STATUSES:
                raise BusinessRuleError(
                    f"Assignee cannot be changed in status '{task.status}'",
                    code="ASSIGNEE_LOCKED",
                    details={"status": task.status},
                )
            if changes["assignee_id"] is not None:
                await self._ensure_assignee_capacity(changes["assignee_id"], task, task.status)

        if "deadline" in changes and changes["deadline"] != task.deadline:
            self._ensure_deadline_not_past(changes["deadline"])

        for field, value in changes.items():
            setattr(task, field, value)
        await self.session.commit()
        return await self.get(task_id)

    async def delete(self, task_id: int, user: User) -> None:
        task = await self._get_for_update(task_id)
        if task.author_id != user.id:
            raise PermissionDeniedError("Only the author can delete the task")
        if task.status in UNDELETABLE_STATUSES:
            raise BusinessRuleError(
                f"Task in status '{task.status}' cannot be deleted; "
                "move it to 'cancelled' or 'done' first",
                code="TASK_NOT_DELETABLE",
                details={"status": task.status},
            )
        await self.tasks.delete(task)
        await self.session.commit()

    # --- helpers -----------------------------------------------------------------

    async def _get_for_update(self, task_id: int) -> Task:
        # Row lock: concurrent edits/status changes of one task are serialized.
        task = await self.tasks.get(task_id, for_update=True)
        if task is None:
            raise self._not_found(task_id)
        return task

    async def _lock_assignee(self, user_id: int) -> User:
        user = await self.users.lock(user_id)
        if user is None:
            raise DomainValidationError(
                "Assignee not found", code="ASSIGNEE_NOT_FOUND", details={"assignee_id": user_id}
            )
        return user

    async def _ensure_assignee_capacity(
        self, assignee_id: int, task: Task, resulting_status: TaskStatus
    ) -> None:
        """Assignee must exist and hold < limit active tasks if this task will be active.

        User row is locked first, so two parallel requests can't both pass the count check.
        """
        await self._lock_assignee(assignee_id)
        if resulting_status not in ACTIVE_STATUSES:
            return
        active = await self.tasks.count_active_for_assignee(assignee_id, exclude_task_id=task.id)
        if active >= MAX_ACTIVE_TASKS_PER_ASSIGNEE:
            raise BusinessRuleError(
                f"Assignee already has {MAX_ACTIVE_TASKS_PER_ASSIGNEE} active tasks",
                code="ASSIGNEE_TASK_LIMIT_EXCEEDED",
                details={"assignee_id": assignee_id, "limit": MAX_ACTIVE_TASKS_PER_ASSIGNEE},
            )

    @staticmethod
    def _ensure_can_edit(task: Task, user: User) -> None:
        if user.id not in (task.author_id, task.assignee_id):
            raise PermissionDeniedError("Only the author or the assignee can modify the task")

    @staticmethod
    def _ensure_deadline_not_past(deadline: datetime | None) -> None:
        if deadline is not None and deadline < datetime.now(UTC):
            raise DomainValidationError(
                "Deadline cannot be in the past",
                code="DEADLINE_IN_PAST",
                details={"deadline": deadline},
            )

    @staticmethod
    def _not_found(task_id: int) -> NotFoundError:
        return NotFoundError("Task not found", code="TASK_NOT_FOUND", details={"task_id": task_id})
