from sqlalchemy import SmallInteger
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.domain.status import TaskPriority


class PriorityType(TypeDecorator[TaskPriority]):
    """Stores TaskPriority as smallint rank so ORDER BY priority is index-friendly (no CASE)."""

    impl = SmallInteger
    cache_ok = True

    def process_bind_param(self, value: TaskPriority | str | None, dialect: Dialect) -> int | None:
        return None if value is None else TaskPriority(value).rank

    def process_result_value(self, value: int | None, dialect: Dialect) -> TaskPriority | None:
        return None if value is None else TaskPriority.from_rank(value)
