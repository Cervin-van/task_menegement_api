from dataclasses import dataclass

from app.domain.status import TaskStatus


@dataclass(frozen=True, slots=True)
class TaskAssigned:
    task_id: int
    title: str
    assignee_email: str


@dataclass(frozen=True, slots=True)
class TaskStatusChanged:
    task_id: int
    title: str
    old_status: TaskStatus
    new_status: TaskStatus
    recipients: tuple[str, ...]


DomainEvent = TaskAssigned | TaskStatusChanged
