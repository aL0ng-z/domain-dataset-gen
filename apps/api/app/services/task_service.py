import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


class TaskService:
    def __init__(self, db: AsyncSession, redis_client: aioredis.Redis | None = None):
        self.db = db
        self.redis = redis_client

    async def create_task(
        self,
        project_id: uuid.UUID,
        task_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        created_by: uuid.UUID,
        parent_task_id: uuid.UUID | None = None,
    ) -> Task:
        task = Task(
            project_id=project_id,
            task_type=task_type,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by=created_by,
            parent_task_id=parent_task_id,
        )
        self.db.add(task)
        await self.db.flush()
        await self.db.refresh(task)
        await self._publish_event(task, "task.created")
        return task

    async def update_status(
        self, task_id: uuid.UUID, status: str, progress: int | None = None, error_message: str | None = None
    ) -> Task | None:
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            return None

        task.status = status
        if progress is not None:
            task.progress = progress
        if error_message is not None:
            task.error_message = error_message
        if status == "processing" and task.started_at is None:
            task.started_at = datetime.now(UTC)
        if status in ("completed", "failed", "cancelled"):
            task.completed_at = datetime.now(UTC)
            if status == "completed":
                task.progress = 100

        await self.db.flush()
        await self.db.refresh(task)

        event = f"task.{status}" if status in ("completed", "failed") else "task.progress"
        await self._publish_event(task, event)
        return task

    async def list_tasks(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20,
        task_type: str | None = None, status: str | None = None,
    ) -> tuple[list[Task], int]:
        offset = (page - 1) * page_size
        base = select(Task).where(Task.project_id == project_id)
        if task_type:
            base = base.where(Task.task_type == task_type)
        if status:
            base = base.where(Task.status == status)

        count_result = await self.db.execute(select(func.count()).select_from(base.subquery()))
        total = count_result.scalar() or 0
        result = await self.db.execute(base.order_by(Task.created_at.desc()).offset(offset).limit(page_size))
        return list(result.scalars().all()), total

    async def get_task(self, task_id: uuid.UUID) -> Task | None:
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def cancel_task(self, task_id: uuid.UUID) -> Task | None:
        return await self.update_status(task_id, "cancelled")

    async def _publish_event(self, task: Task, event: str) -> None:
        if self.redis is None:
            return
        import json

        message = json.dumps({
            "event": event,
            "task_id": str(task.id),
            "task_type": task.task_type,
            "status": task.status,
            "progress": task.progress,
            "entity_type": task.entity_type,
            "entity_id": str(task.entity_id),
            "timestamp": datetime.now(UTC).isoformat(),
        })
        await self.redis.publish(f"project:{task.project_id}:tasks", message)
