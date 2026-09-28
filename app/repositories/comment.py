from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Comment
from app.schemas.common import PageParams


class CommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, comment: Comment) -> Comment:
        self.session.add(comment)
        await self.session.flush()
        return comment

    async def get(self, comment_id: int) -> Comment | None:
        return await self.session.scalar(
            select(Comment)
            .where(Comment.id == comment_id)
            .options(selectinload(Comment.author))
            .execution_options(populate_existing=True)
        )

    async def list_for_task(
        self, task_id: int, params: PageParams
    ) -> tuple[Sequence[Comment], int]:
        total = await self.session.scalar(
            select(func.count()).select_from(Comment).where(Comment.task_id == task_id)
        )
        items = await self.session.scalars(
            select(Comment)
            .where(Comment.task_id == task_id)
            .options(selectinload(Comment.author))
            # Chronological thread; matches ix_comments_task_id_created_at.
            .order_by(Comment.created_at.asc(), Comment.id.asc())
            .offset(params.offset)
            .limit(params.size)
        )
        return items.all(), total or 0
