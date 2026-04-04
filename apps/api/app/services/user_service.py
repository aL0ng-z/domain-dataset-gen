import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_users(self, page: int = 1, page_size: int = 20) -> tuple[list[User], int]:
        offset = (page - 1) * page_size
        count_result = await self.db.execute(select(func.count()).select_from(User))
        total = count_result.scalar() or 0

        result = await self.db.execute(
            select(User).order_by(User.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def update_user(self, user_id: uuid.UUID, **kwargs) -> User | None:
        user = await self.get_user(user_id)
        if user is None:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(user, key, value)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def delete_user(self, user_id: uuid.UUID) -> bool:
        user = await self.get_user(user_id)
        if user is None:
            return False
        user.is_active = False
        await self.db.flush()
        return True
