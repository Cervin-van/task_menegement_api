from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import PriorityType
from app.domain.status import TaskPriority, TaskStatus
from app.models.mixins import TimestampMixin
from app.models.user import User


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", values_callable=lambda e: [m.value for m in e]),
        default=TaskStatus.BACKLOG,
        server_default=TaskStatus.BACKLOG.value,
        nullable=False,
        index=True,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        PriorityType(), default=TaskPriority.MEDIUM, nullable=False
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    # lazy="raise": no implicit IO in async code, loading must be explicit.
    author: Mapped[User] = relationship(foreign_keys=[author_id], lazy="raise")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id], lazy="raise")

    __table_args__ = (
        # ILIKE '%q%' search on title/description.
        Index(
            "ix_tasks_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "ix_tasks_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )


# Default list ordering: priority DESC, deadline ASC NULLS LAST.
Index("ix_tasks_priority_deadline", Task.priority.desc(), Task.deadline.asc().nulls_last())
