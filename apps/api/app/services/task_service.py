"""任务服务（T07 §6 API 合同）。

业务触发 API 在事务中创建业务 job + Task，提交后由 runner 领取；不再调用
``background_tasks.add_task``。TaskService 提供创建/查询/取消/重试/Attempt 查询，
并发布版本化 WebSocket 事件（事件只用于提示刷新，REST 始终是真源）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskAttempt
from app.workers.queue import TERMINAL_STATUSES, TaskQueue

# 业务任务类型 -> handler 名 / payload version / 默认策略。
# max_attempts = TaskPolicy.max_retries + 1（默认 1+1=2，可覆盖）。
DEFAULT_MAX_ATTEMPTS: dict[str, int] = {
    "parse": 2,
    "clean": 2,
    "chunk": 2,
    "generate": 1,
    "generate_batch": 2,
    "export": 2,
}
DEFAULT_TIMEOUT_SECONDS: dict[str, int] = {
    "parse": 300,
    "clean": 300,
    "chunk": 300,
    "generate": 120,
    "generate_batch": 900,
    "export": 300,
}

# task_type -> handler 名（payload version 均为 1）。
TASK_TYPE_TO_HANDLER: dict[str, str] = {
    "parse": "parse_document",
    "clean": "clean_document",
    "chunk": "chunk_document",
    "generate": "generate_single",
    "generate_batch": "generate_batch",
    "export_dataset": "export_dataset",
    "export_benchmark": "export_benchmark",
}

# T06：chunk handler 升级为 v2（payload 携带 chunk_set_id，冻结输入）。
TASK_TYPE_TO_PAYLOAD_VERSION: dict[str, int] = {
    "chunk": 2,
}


class RetryPreparationError(Exception):
    """业务实体无法与人工 retry 原子衔接。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


RetryPreparer = Callable[[AsyncSession, Task, Task], Awaitable[None]]


class RetryPreparationRegistry:
    """handler/version -> 业务对象 retry 准备钩子。"""

    def __init__(self) -> None:
        self._preparers: dict[tuple[str, int], RetryPreparer] = {}

    def register(self, handler: str, version: int, preparer: RetryPreparer) -> None:
        self._preparers[(handler, version)] = preparer

    def resolve(self, handler: str, version: int) -> RetryPreparer | None:
        return self._preparers.get((handler, version))


retry_preparation_registry = RetryPreparationRegistry()


async def _prepare_export_retry(db: AsyncSession, source_task: Task, new_task: Task) -> None:
    """让同一 Export 原子指向新 Task；已封存 snapshot 保持不变。"""
    from app.models.export import Export

    raw_export_id = (source_task.payload or {}).get("export_id")
    if raw_export_id is None:
        raise RetryPreparationError("EXPORT_RETRY_INVALID", "导出任务缺少 export_id")
    try:
        export_id = uuid.UUID(str(raw_export_id))
    except ValueError as exc:
        raise RetryPreparationError("EXPORT_RETRY_INVALID", "导出任务 export_id 非法") from exc

    export = (
        await db.execute(select(Export).where(Export.id == export_id).with_for_update())
    ).scalar_one_or_none()
    if export is None or export.project_id != source_task.project_id:
        raise RetryPreparationError("EXPORT_NOT_FOUND", "导出记录不存在")
    if export.status == "completed":
        raise RetryPreparationError("EXPORT_IMMUTABLE", "已完成导出不可重试")
    if export.status != "failed" or export.task_id != source_task.id:
        raise RetryPreparationError("EXPORT_NOT_RETRYABLE", "导出记录不处于可重试状态")

    now = datetime.now(UTC)
    result = await db.execute(
        update(Export)
        .where(
            Export.id == export.id,
            Export.status == "failed",
            Export.task_id == source_task.id,
        )
        .values(
            status="queued",
            task_id=new_task.id,
            retry_count=Export.retry_count + 1,
            error_code=None,
            error_message=None,
            completed_at=None,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        raise RetryPreparationError("EXPORT_RETRY_CONFLICT", "导出重试发生并发冲突")


for _export_handler in ("export_dataset", "export_benchmark"):
    retry_preparation_registry.register(_export_handler, 1, _prepare_export_retry)


class TaskService:
    def __init__(self, db: AsyncSession, redis_client: aioredis.Redis | None = None):
        self.db = db
        self.redis = redis_client
        self.queue = TaskQueue(db)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_task(
        self,
        project_id: uuid.UUID,
        task_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        created_by: uuid.UUID,
        *,
        payload: dict[str, Any] | None = None,
        handler: str | None = None,
        parent_task_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        timeout_seconds: int | None = None,
        next_run_at: datetime | None = None,
        task_subtype: str | None = None,
    ) -> Task:
        """创建持久化 Task（调用方事务内，不自行 commit）。

        task_subtype 用于区分同 task_type 下的不同 handler（如 export -> dataset/benchmark）。

        幂等：调用方提供 Idempotency-Key 时，同 key 且请求摘要一致返回既有 Task；
        同 key 但请求摘要不同返回 409（由调用方据此抛错）。
        """
        resolved_handler = handler or TASK_TYPE_TO_HANDLER.get(task_subtype or task_type, task_type)
        resolved_payload = payload or {}
        resolved_payload_version = TASK_TYPE_TO_PAYLOAD_VERSION.get(task_subtype or task_type, 1)
        if idempotency_key:
            existing = await self._find_by_idempotency(project_id, task_type, idempotency_key)
            if existing is not None:
                return existing
        return await self.queue.create_task(
            project_id=project_id,
            task_type=task_type,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by=created_by,
            handler=resolved_handler,
            payload=resolved_payload,
            payload_version=resolved_payload_version,
            parent_task_id=parent_task_id,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts or DEFAULT_MAX_ATTEMPTS.get(task_type, 2),
            timeout_seconds=timeout_seconds or DEFAULT_TIMEOUT_SECONDS.get(task_type, 300),
            next_run_at=next_run_at,
        )

    async def _find_by_idempotency(
        self, project_id: uuid.UUID, task_type: str, idempotency_key: str
    ) -> Task | None:
        result = await self.db.execute(
            select(Task).where(
                Task.project_id == project_id,
                Task.task_type == task_type,
                Task.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_task(self, task_id: uuid.UUID) -> Task | None:
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def list_tasks(
        self, project_id: uuid.UUID, page: int = 1, page_size: int = 20,
        task_type: str | None = None, status: str | None = None,
    ) -> tuple[list[Task], int]:
        from sqlalchemy import func

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

    async def list_attempts(self, task_id: uuid.UUID) -> list[TaskAttempt]:
        result = await self.db.execute(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task_id)
            .order_by(TaskAttempt.attempt_no)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_task(
        self, task_id: uuid.UUID, cancel_requested_by: uuid.UUID
    ) -> Task | None:
        """取消任务：queued 原子转 cancelled；processing 转 cancelling。

        重复请求幂等返回当前状态；终态返回当前对象且不改写完成时间。
        父任务取消向所有非终态子任务传播（CAS 聚合）。
        """
        ok, status = await self.queue.request_cancel(
            task_id=task_id, cancel_requested_by=cancel_requested_by
        )
        if not ok:
            return None
        # 父任务取消传播：向所有非终态子任务请求取消。
        if status in ("cancelled", "cancelling"):
            await self._cancel_children(task_id, cancel_requested_by)
            # T06：chunk Task queued 取消后，关联 ChunkSet 原子收敛为 cancelled。
            await self._converge_chunk_set_on_cancel(task_id)
        task = await self.get_task(task_id)
        if task is not None:
            await self._publish_event(task, f"task.{task.status}")
        return task

    async def _converge_chunk_set_on_cancel(self, task_id: uuid.UUID) -> None:
        """queued chunk Task 取消后把关联 ChunkSet 收敛为 cancelled（不覆盖已完成产物）。"""
        task = await self.get_task(task_id)
        if task is None or task.task_type != "chunk":
            return
        chunk_set_id = (task.payload or {}).get("chunk_set_id")
        if not chunk_set_id:
            return
        from datetime import UTC, datetime

        from sqlalchemy import update

        from app.models.chunk_set import ChunkSet

        await self.db.execute(
            update(ChunkSet)
            .where(
                ChunkSet.id == uuid.UUID(str(chunk_set_id)),
                ChunkSet.status.in_(("pending", "processing", "review_pending")),
            )
            .values(status="cancelled", error_message="切分任务已取消", completed_at=datetime.now(UTC))
        )

    async def _cancel_children(
        self, parent_task_id: uuid.UUID, cancel_requested_by: uuid.UUID
    ) -> None:
        """向所有非终态子任务传播取消请求。"""
        result = await self.db.execute(
            select(Task).where(
                Task.parent_task_id == parent_task_id,
                Task.status.in_(("queued", "processing", "cancelling")),
            )
        )
        children = list(result.scalars().all())
        for child in children:
            await self.queue.request_cancel(
                task_id=child.id, cancel_requested_by=cancel_requested_by
            )
        if children:
            for child in children:
                await self._publish_event(child, f"task.{child.status}")

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    async def retry_task(
        self,
        source_task: Task,
        *,
        idempotency_key: str,
        created_by: uuid.UUID,
    ) -> tuple[Task | None, bool]:
        """人工 retry：创建 retry_of_task_id=source 的新 queued Task。

        返回 (新 Task, 是否新建)。若已存在非终态后继返回 (已存在后继, False)，
        调用方决定 200/409 幂等语义。
        """
        new_task, newly_created = await self.queue.create_retry(
            source_task_id=source_task.id,
            idempotency_key=idempotency_key,
            project_id=source_task.project_id,
            task_type=source_task.task_type,
            entity_type=source_task.entity_type,
            entity_id=source_task.entity_id,
            created_by=created_by,
            handler=source_task.handler,
            payload=source_task.payload or {},
            payload_version=source_task.payload_version or 1,
            max_attempts=source_task.max_attempts,
            timeout_seconds=source_task.timeout_seconds,
        )
        if new_task is None:
            return None, False
        if not newly_created:
            # 同一个 Idempotency-Key 是安全重放；不同 key 命中既有活跃后继则冲突。
            if new_task.idempotency_key != idempotency_key:
                return None, False
            return new_task, False

        preparer = retry_preparation_registry.resolve(
            source_task.handler, source_task.payload_version or 1
        )
        if preparer is not None:
            await preparer(self.db, source_task, new_task)
        if new_task.status == "queued":
            await self._publish_event(new_task, "task.created")
        return new_task, True

    # ------------------------------------------------------------------
    # 事件发布（版本化，仅提示刷新；REST 是真源）
    # ------------------------------------------------------------------

    async def publish_transition(self, task: Task, event: str) -> None:
        await self._publish_event(task, event)

    async def _publish_event(self, task: Task, event: str) -> None:
        if self.redis is None:
            return
        message = json.dumps({
            "event": event,
            "task_id": str(task.id),
            "task_type": task.task_type,
            "status": task.status,
            "progress": task.progress,
            "state_version": task.state_version,
            "attempt_count": task.attempt_count,
            "event_at": datetime.now(UTC).isoformat(),
        })
        # 事件发布失败不阻断业务（Redis 不可用时 REST 仍是真源）。
        import contextlib

        with contextlib.suppress(Exception):
            await self.redis.publish(f"project:{task.project_id}:tasks", message)


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES
