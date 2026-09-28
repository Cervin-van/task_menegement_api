from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.domain.status import TaskPriority, TaskStatus
from app.schemas.common import DbDatetime, DbId, DbIdQuery, PageParams, SafeStr

Title = Annotated[SafeStr, Field(min_length=1, max_length=255)]
# Whitespace-only description (stripped to "") is stored as NULL, not as an empty string.
Description = Annotated[SafeStr, Field(max_length=10_000), AfterValidator(lambda v: v or None)]


class UserShort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str


class TaskCreate(BaseModel):
    # forbid: e.g. {"status": "done"} must not be silently ignored (status has its own endpoint).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Title
    description: Description | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    assignee_id: DbId | None = None
    # Aware datetime only: naive datetimes are rejected -> no ambiguity about timezone.
    deadline: DbDatetime | None = None


class TaskUpdate(BaseModel):
    """Partial update. Status is changed only via PATCH /tasks/{id}/status."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Title | None = None
    description: Description | None = None
    priority: TaskPriority | None = None
    assignee_id: DbId | None = None
    deadline: DbDatetime | None = None

    @model_validator(mode="after")
    def _non_nullable_fields(self) -> Self:
        # null is allowed to clear description/assignee/deadline, but not title/priority.
        for field in ("title", "priority"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TaskStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus


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


class TaskSortField(StrEnum):
    CREATED_AT = "created_at"
    DEADLINE = "deadline"
    PRIORITY = "priority"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class TaskListQuery(PageParams):
    """Query params of GET /tasks. Without sort_by: priority high->low, then nearest deadline."""

    model_config = ConfigDict(extra="forbid")

    search: Annotated[SafeStr, Field(min_length=1, max_length=100)] | None = None
    status: list[TaskStatus] = Field(default_factory=list)
    priority: list[TaskPriority] = Field(default_factory=list)
    assignee_id: DbIdQuery | None = None
    deadline_from: DbDatetime | None = None
    deadline_to: DbDatetime | None = None
    sort_by: TaskSortField | None = None
    order: SortOrder | None = None

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        if self.deadline_from and self.deadline_to and self.deadline_from > self.deadline_to:
            raise ValueError("deadline_from must be <= deadline_to")
        if self.order is not None and self.sort_by is None:
            raise ValueError("order requires sort_by")
        return self


class TaskStats(BaseModel):
    total: int
    by_status: dict[TaskStatus, int]
    by_priority: dict[TaskPriority, int]
    overdue: int
    active: int
