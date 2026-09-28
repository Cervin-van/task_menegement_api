from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domain.events import DomainEvent, TaskAssigned, TaskStatusChanged
from app.domain.status import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    ASSIGNEE_LOCKED_STATUSES,
    FINAL_STATUSES,
    MAX_ACTIVE_TASKS_PER_ASSIGNEE,
    UNDELETABLE_STATUSES,
    TaskPriority,
    TaskStatus,
    can_transition,
)
from app.models import Task, User
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.schemas.common import PageParams
from app.schemas.task import TaskCreate, TaskListQuery, TaskStats, TaskUpdate


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.users = UserRepository(session)
        # Collected after successful commit; dispatched by the API layer in background.
        self.events: list[DomainEvent] = []

    def pop_events(self) -> list[DomainEvent]:
        events, self.events = self.events, []
        return events

    async def create(self, data: TaskCreate, author: User) -> Task:
        self._ensure_deadline_not_past(data.deadline)
        if data.assignee_id is not None:
            # New task is always backlog (not active) -> no limit, no need to lock the user row.
            await self._ensure_assignee_exists(data.assignee_id)

        task = Task(
            **data.model_dump(),
            author_id=author.id,
            status=TaskStatus.BACKLOG,
        )
        await self.tasks.add(task)
        await self.session.commit()
        task = await self.get(task.id)
        self._record_assignment(task, actor=author)
        return task

    async def get(self, task_id: int) -> Task:
        task = await self.tasks.get(task_id)
        if task is None:
            raise self._not_found(task_id)
        return task

    async def list_tasks(self, query: TaskListQuery) -> tuple[Sequence[Task], int]:
        return await self.tasks.list_tasks(query)

    async def list_overdue(self, params: PageParams) -> tuple[Sequence[Task], int]:
        return await self.tasks.list_overdue(datetime.now(UTC), params)

    async def stats(self) -> TaskStats:
        row = await self.tasks.stats(datetime.now(UTC))
        return TaskStats(
            total=row["total"],
            by_status={s: row[f"status_{s}"] for s in TaskStatus},
            by_priority={p: row[f"priority_{p}"] for p in TaskPriority},
            overdue=row["overdue"],
            active=row["active"],
        )

    async def update(self, task_id: int, data: TaskUpdate, user: User) -> Task:
        task = await self._get_for_update(task_id)
        self._ensure_can_edit(task, user)
        self._ensure_not_final(task)

        changes = data.model_dump(exclude_unset=True)
        assignee_changed = "assignee_id" in changes and changes["assignee_id"] != task.assignee_id

        if assignee_changed:
            if user.id != task.author_id:
                # Otherwise an assignee could hand the task to anyone and lose access to it.
                raise PermissionDeniedError(
                    "Only the author can change the assignee", code="ASSIGNEE_CHANGE_FORBIDDEN"
                )
            if task.status in ASSIGNEE_LOCKED_STATUSES:
                raise BusinessRuleError(
                    f"Assignee cannot be changed in status '{task.status}'",
                    code="ASSIGNEE_LOCKED",
                    details={"status": task.status},
                )
            if changes["assignee_id"] is not None:
                await self._ensure_assignee_capacity(changes["assignee_id"], task, task.status)

        if "deadline" in changes and changes["deadline"] != task.deadline:
            if user.id != task.author_id:
                # Otherwise an assignee could extend an overdue task and still close it as done.
                raise PermissionDeniedError(
                    "Only the author can change the deadline", code="DEADLINE_CHANGE_FORBIDDEN"
                )
            overdue = task.deadline is not None and task.deadline < datetime.now(UTC)
            if overdue and changes["deadline"] is None:
                raise BusinessRuleError(
                    "Deadline of an overdue task can be moved, not removed",
                    code="OVERDUE_DEADLINE_REMOVAL_FORBIDDEN",
                )
            self._ensure_deadline_not_past(changes["deadline"])

        for field, value in changes.items():
            setattr(task, field, value)
        await self.session.commit()
        task = await self.get(task_id)
        if assignee_changed:
            self._record_assignment(task, actor=user)
        return task

    async def change_status(self, task_id: int, target: TaskStatus, user: User) -> Task:
        task = await self._get_for_update(task_id)
        self._ensure_can_edit(task, user)
        self._ensure_not_final(task)

        current = task.status
        if not can_transition(current, target):
            raise BusinessRuleError(
                f"Transition '{current}' -> '{target}' is not allowed",
                code="INVALID_STATUS_TRANSITION",
                details={
                    "from": current,
                    "to": target,
                    "allowed": sorted(ALLOWED_TRANSITIONS[current]),
                },
            )

        if target is TaskStatus.REVIEW and task.assignee_id is None:
            # Assignee is locked in review and done requires one: without this check
            # the task would be stuck in review forever.
            raise BusinessRuleError(
                "Task cannot go to review without an assignee", code="REVIEW_REQUIRES_ASSIGNEE"
            )

        if target is TaskStatus.DONE:
            if task.assignee_id is None:
                raise BusinessRuleError(
                    "Task cannot be done without an assignee", code="DONE_REQUIRES_ASSIGNEE"
                )
            if task.deadline is not None and task.deadline < datetime.now(UTC):
                raise BusinessRuleError(
                    "Task cannot be done after its deadline has passed",
                    code="DONE_AFTER_DEADLINE",
                    details={"deadline": task.deadline},
                )

        # Entering the active set (backlog -> todo) consumes a slot of the assignee's limit.
        if (
            target in ACTIVE_STATUSES
            and current not in ACTIVE_STATUSES
            and task.assignee_id is not None
        ):
            await self._ensure_assignee_capacity(task.assignee_id, task, target)

        task.status = target
        await self.session.commit()
        task = await self.get(task_id)

        recipients = self._recipients(task, actor=user)
        if recipients:
            self.events.append(
                TaskStatusChanged(
                    task_id=task.id,
                    title=task.title,
                    old_status=current,
                    new_status=target,
                    recipients=recipients,
                )
            )
        return task

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

    async def _ensure_assignee_exists(self, user_id: int) -> None:
        if await self.users.get_by_id(user_id) is None:
            raise self._assignee_not_found(user_id)

    async def _lock_assignee(self, user_id: int) -> User:
        user = await self.users.lock(user_id)
        if user is None:
            raise self._assignee_not_found(user_id)
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

    def _record_assignment(self, task: Task, actor: User) -> None:
        if task.assignee is not None and task.assignee.id != actor.id:
            self.events.append(
                TaskAssigned(task_id=task.id, title=task.title, assignee_email=task.assignee.email)
            )

    @staticmethod
    def _recipients(task: Task, actor: User) -> tuple[str, ...]:
        """Author and assignee, except whoever made the change."""
        people = [task.author, task.assignee]
        emails = {p.email for p in people if p is not None and p.id != actor.id}
        return tuple(sorted(emails))

    @staticmethod
    def _ensure_not_final(task: Task) -> None:
        if task.status in FINAL_STATUSES:
            raise BusinessRuleError(
                f"Task in status '{task.status}' cannot be edited",
                code="TASK_NOT_EDITABLE",
                details={"status": task.status},
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
    def _assignee_not_found(user_id: int) -> DomainValidationError:
        return DomainValidationError(
            "Assignee not found", code="ASSIGNEE_NOT_FOUND", details={"assignee_id": user_id}
        )

    @staticmethod
    def _not_found(task_id: int) -> NotFoundError:
        return task_not_found(task_id)


def task_not_found(task_id: int) -> NotFoundError:
    return NotFoundError("Task not found", code="TASK_NOT_FOUND", details={"task_id": task_id})
