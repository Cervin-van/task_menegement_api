from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def lock(self, user_id: int) -> User | None:
        """SELECT ... FOR NO KEY UPDATE: serializes concurrent assignments to the same user.

        NO KEY: doesn't block inserts that only reference the user via FK (new tasks/comments).
        """
        return await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update(key_share=True)
        )

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email))

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user
