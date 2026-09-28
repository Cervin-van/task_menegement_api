from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Comment, User
from app.repositories.comment import CommentRepository
from app.repositories.task import TaskRepository
from app.schemas.comment import CommentCreate
from app.schemas.common import PageParams
from app.services.task import task_not_found


class CommentService:
    """Any authenticated user may comment, including on done/cancelled tasks:
    a comment is a discussion entry, not an edit of the task itself."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.comments = CommentRepository(session)
        self.tasks = TaskRepository(session)

    async def add(self, task_id: int, data: CommentCreate, author: User) -> Comment:
        await self._ensure_task_exists(task_id)
        comment = await self.comments.add(
            Comment(task_id=task_id, author_id=author.id, text=data.text)
        )
        await self.session.commit()
        # Re-read with author loaded (relationships are lazy="raise").
        created = await self.comments.get(comment.id)
        if created is None:  # deleted concurrently together with its task (CASCADE)
            raise task_not_found(task_id)
        return created

    async def list_for_task(
        self, task_id: int, params: PageParams
    ) -> tuple[Sequence[Comment], int]:
        await self._ensure_task_exists(task_id)
        return await self.comments.list_for_task(task_id, params)

    async def _ensure_task_exists(self, task_id: int) -> None:
        if not await self.tasks.exists(task_id):
            raise task_not_found(task_id)
