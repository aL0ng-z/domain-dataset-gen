import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.models.config import ParserProfile, TaskPolicy


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
        if self.model_class is TaskPolicy:
            # A project row lock serializes category changes as well as per-type defaults.
            from app.models.project import Project
            await self.db.execute(select(Project.id).where(Project.id == obj.project_id).with_for_update())
            await self.db.refresh(obj)
            for task_type in sorted({obj.task_type, mapped.get("task_type") or obj.task_type}):
                await self._lock_task_policy(obj.project_id, task_type)
            await self.db.refresh(obj)
            if mapped.get("task_type", obj.task_type) != obj.task_type:
                obj.is_default = False  # Defaults belong to their original task category.
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

    async def _lock_task_policy(self, project_id: uuid.UUID, task_type: str) -> None:
        await self.db.execute(text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
        ), {"key": f"task-policy:{project_id}:{task_type}"})

    async def set_default(self, project_id: uuid.UUID, config_id: uuid.UUID) -> Any | None:
        obj = await self.get(config_id)
        if obj is None or obj.project_id != project_id:
            return None
        query = select(self.model_class).where(
            self.model_class.project_id == project_id, self.model_class.is_default.is_(True),
        )
        if self.model_class is TaskPolicy:
            from app.models.project import Project
            await self.db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
            await self.db.refresh(obj)
            await self._lock_task_policy(project_id, obj.task_type)
            await self.db.refresh(obj)
            query = query.where(TaskPolicy.task_type == obj.task_type)
        result = await self.db.execute(query.execution_options(populate_existing=True))
        for existing in result.scalars().all():
            existing.is_default = False
        # Clear first to satisfy the partial unique index irrespective of ORM update order.
        await self.db.flush()
        obj.is_default = True
        await self.db.flush()
        await self.db.refresh(obj)
        return obj


class ParserProfileService(ConfigService):
    """ParserProfile 专用 CRUD：endpoint_ref 服务端解析 + parser_options 白名单收紧。"""

    def __init__(self, db: AsyncSession):
        super().__init__(db, ParserProfile)

    def _validate_for_parser(self, parser_name: str, options: dict | None) -> dict | None:
        """按 parser_name 校验并收紧 parser_options；返回清理后的副本。

        - 递归拒绝网络/秘密字段（base_url/upload_url/token 等）；
        - 拒绝白名单外未知字段；
        - endpoint_ref 必须存在、启用且 parser_name 匹配（在 create/update 内执行）。
        """
        from app.schemas.config import finalize_parser_options

        return finalize_parser_options(parser_name, options)

    def _resolve_endpoint_ref(self, parser_name: str, options: dict | None) -> None:
        """endpoint_ref 必须存在、启用且 parser_name 匹配；否则抛 ValueError。"""
        from app.security.registry import get_registry

        endpoint_ref = (options or {}).get("endpoint_ref")
        registry = get_registry()
        if parser_name in ("pymupdf4llm", "mock"):
            return
        if not endpoint_ref:
            raise ValueError("invalid_parser_endpoint: 缺少 endpoint_ref")
        definition = registry.get(str(endpoint_ref))
        if definition is None:
            raise ValueError("invalid_parser_endpoint: 未知端点")
        if definition.parser_name != parser_name:
            raise ValueError("invalid_parser_endpoint: 解析器类型不匹配")

    async def create(self, project_id: uuid.UUID, **kwargs) -> ParserProfile:
        parser_name = kwargs.get("parser_name", "mock")
        options = kwargs.get("parser_options")
        # 先做白名单收紧与禁止字段拒绝（在 DB 写入前 fail closed）。
        cleaned = self._validate_for_parser(parser_name, options)
        self._resolve_endpoint_ref(parser_name, cleaned)
        kwargs["parser_options"] = cleaned
        return await super().create(project_id, **kwargs)

    async def update(self, config_id: uuid.UUID, **kwargs) -> ParserProfile | None:
        obj = await self.get(config_id)
        if obj is None:
            return None
        parser_name = kwargs.get("parser_name", obj.parser_name)
        if kwargs.get("parser_options") is not None:
            cleaned = self._validate_for_parser(parser_name, kwargs["parser_options"])
            self._resolve_endpoint_ref(parser_name, cleaned)
            kwargs["parser_options"] = cleaned
        return await super().update(config_id, **kwargs)
