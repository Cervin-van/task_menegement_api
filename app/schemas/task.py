from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domain.status import TaskPriority, TaskStatus


class UserShort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee_id: int | None = None
    # AwareDatetime: naive datetimes are rejected -> no ambiguity about timezone.
    deadline: AwareDatetime | None = None


class TaskUpdate(BaseModel):
    """Partial update. Status is changed only via PATCH /tasks/{id}/status."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    priority: TaskPriority | None = None
    assignee_id: int | None = None
    deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def _non_nullable_fields(self) -> Self:
        # null is allowed to clear description/assignee/deadline, but not title/priority.
        for field in ("title", "priority"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    author: UserShort
    assignee: UserShort | None
    deadline: datetime | None
    created_at: datetime
    updated_at: datetime
