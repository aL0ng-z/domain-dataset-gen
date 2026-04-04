import uuid
from typing import Any, Type

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base


class ConfigService:
    """Generic CRUD service for config profile tables."""

    def __init__(self, db: AsyncSession, model_class: Type[Base]):
        self.db = db
        self.model_class = model_class

    async def create(self, project_id: uuid.UUID, **kwargs) -> Any:
        obj = self.model_class(project_id=project_id, **kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def list(self, project_id: uuid.UUID, page: int = 1, page_size: int = 20) -> tuple[list, int]:
        offset = (page - 1) * page_size
        base = select(self.model_class).where(self.model_class.project_id == project_id)
        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(base.order_by(self.model_class.created_at.desc()).offset(offset).limit(page_size))
        return list(result.scalars().all()), total

    async def get(self, config_id: uuid.UUID) -> Any | None:
        result = await self.db.execute(select(self.model_class).where(self.model_class.id == config_id))
        return result.scalar_one_or_none()

    async def update(self, config_id: uuid.UUID, **kwargs) -> Any | None:
        obj = await self.get(config_id)
        if obj is None:
            return None
        for key, value in kwargs.items():
            if value is not None:
                setattr(obj, key, value)
        obj.version = obj.version + 1
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete(self, config_id: uuid.UUID) -> bool:
        obj = await self.get(config_id)
        if obj is None:
            return False
        await self.db.delete(obj)
        await self.db.flush()
        return True

    async def set_default(self, project_id: uuid.UUID, config_id: uuid.UUID) -> Any | None:
        # Clear existing default
        result = await self.db.execute(
            select(self.model_class).where(
                self.model_class.project_id == project_id, self.model_class.is_default == True  # noqa: E712
            )
        )
        for existing in result.scalars().all():
            existing.is_default = False

        # Set new default
        obj = await self.get(config_id)
        if obj is None:
            return None
        obj.is_default = True
        await self.db.flush()
        await self.db.refresh(obj)
        return obj
