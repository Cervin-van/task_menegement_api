from enum import StrEnum


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        """Numeric weight stored in DB: ORDER BY priority DESC -> high, medium, low."""
        return _PRIORITY_RANK[self]

    @classmethod
    def from_rank(cls, rank: int) -> "TaskPriority":
        return _RANK_PRIORITY[rank]


_PRIORITY_RANK = {TaskPriority.LOW: 1, TaskPriority.MEDIUM: 2, TaskPriority.HIGH: 3}
_RANK_PRIORITY = {v: k for k, v in _PRIORITY_RANK.items()}

# State machine as data: only forward moves; anything else (incl. rollback) is rejected.
ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    # backlog -> in_progress: explicit example in the TZ; todo stays an optional planning step.
    TaskStatus.BACKLOG: frozenset({TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.TODO: frozenset({TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.IN_PROGRESS: frozenset({TaskStatus.REVIEW, TaskStatus.CANCELLED}),
    TaskStatus.REVIEW: frozenset({TaskStatus.DONE}),
    TaskStatus.DONE: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

ACTIVE_STATUSES = frozenset({TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW})
FINAL_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.CANCELLED})
ASSIGNEE_LOCKED_STATUSES = frozenset({TaskStatus.REVIEW, TaskStatus.DONE})
UNDELETABLE_STATUSES = frozenset({TaskStatus.IN_PROGRESS, TaskStatus.REVIEW})

MAX_ACTIVE_TASKS_PER_ASSIGNEE = 10


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
