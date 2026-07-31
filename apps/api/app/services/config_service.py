import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base


class ConfigService:
    """Generic CRUD service for config profile tables."""

    def __init__(self, db: AsyncSession, model_class: type[Base]):
        self.db = db
        self.model_class = model_class

    # Map schema field names to model column names where they differ
    FIELD_MAPPINGS = {
        "api_key": "api_key_encrypted",
    }

    def _map_fields(self, kwargs: dict) -> dict:
        """Remap schema field names to model column names."""
        mapped = {}
        for key, value in kwargs.items():
            mapped_key = self.FIELD_MAPPINGS.get(key, key)
            mapped[mapped_key] = value
        return mapped

    async def create(self, project_id: uuid.UUID, **kwargs) -> Any:
        mapped = self._map_fields(kwargs)
        obj = self.model_class(project_id=project_id, **mapped)
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
        mapped = self._map_fields(kwargs)
        for key, value in mapped.items():
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
