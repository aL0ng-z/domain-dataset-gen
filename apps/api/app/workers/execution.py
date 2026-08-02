"""ExecutionContext 与 handler 契约（T07 §5）。

handler 注册表以稳定名和 payload version 分派：``parse_document:v1``、
``clean_document:v1``、``chunk_document:v1``、``generate_single:v1``、
``generate_batch:v1``、``export_dataset:v1``、``export_benchmark:v1``。

每个 handler 接收 ExecutionContext，在外部调用前后、批次循环和发布事务前调用
``checkpoint()``；它校验 run token、lease、timeout 和 cancel request。

可重试错误仅包括明确的网络/限流/临时基础设施失败；校验失败、资源不存在、
合同错误为永久失败（见 workers/errors.py）。

未知 handler/version 为永久失败 UNSUPPORTED_TASK_PAYLOAD，不得无限重试。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.workers.queue import TaskQueue

logger = logging.getLogger(__name__)


class TaskCancelledError(Exception):
    """协作式取消：checkpoint 检测到 cancel 请求，handler 应中止并抛此异常。"""


class TaskTimeoutError(Exception):
    """执行超时：checkpoint 检测到已超过 deadline。"""


class TaskProtocolError(Exception):
    """run token 失效 / lease 过期：worker 不得继续，也不能覆盖终态。"""


@dataclass
class ExecutionContext:
    """handler 执行上下文：携带 run token、lease 与取消/超时检测。"""

    task_id: uuid.UUID
    run_token: uuid.UUID
    project_id: uuid.UUID
    attempt_no: int
    worker_id: str
    payload: dict[str, Any]
    payload_version: int
    db: AsyncSession
    queue: TaskQueue
    deadline: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(seconds=300))
    _cancelled: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    async def checkpoint(self, *, refresh: bool = True) -> None:
        """在外部调用前后、批次循环和发布事务前调用。

        校验：任务仍由本 run token 持有；lease 未过期；未到 deadline；
        未收到 cancel 请求。任一失败抛异常，worker 捕获后不再发布业务产物。
        """
        if self._cancelled:
            raise TaskCancelledError("任务已请求取消")
        if datetime.now(UTC) >= self.deadline:
            raise TaskTimeoutError("任务执行超时")

        if refresh:
            task = (
                await self.db.execute(select(Task).where(Task.id == self.task_id))
            ).scalar_one_or_none()
            if task is None:
                raise TaskProtocolError("任务不存在")
            if task.run_token != self.run_token:
                raise TaskProtocolError("run token 已失效：任务已被重新领取或已终态")
            if task.status == "cancelling" or task.cancel_requested_at is not None:
                self._cancelled = True
                raise TaskCancelledError("任务已请求取消")
            if task.status == "queued":
                # 已被 reaper 回队（竞态）：本 attempt 作废。
                raise TaskProtocolError("任务已回队，当前 attempt 作废")

    def heartbeat(self) -> Awaitable[bool]:
        """刷新 lease（长时间外部调用期间）。"""
        return self.queue.heartbeat(task_id=self.task_id, run_token=self.run_token)

    # ------------------------------------------------------------------
    # 业务写辅助
    # ------------------------------------------------------------------

    def checkpoint_before_publish(self) -> Awaitable[None]:
        """发布事务前门禁：校验 run token + cancel，防止取消后仍发布产物。"""
        return self.checkpoint()


HandlerFunc = Callable[[ExecutionContext], Awaitable[None]]


class HandlerRegistry:
    """稳定名 + payload version -> handler 的注册表。"""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], HandlerFunc] = {}

    def register(self, name: str, version: int) -> Callable[[HandlerFunc], HandlerFunc]:
        def decorator(fn: HandlerFunc) -> HandlerFunc:
            key = (name, version)
            if key in self._handlers:
                raise ValueError(f"handler 重复注册: {name}:v{version}")
            self._handlers[key] = fn
            return fn

        return decorator

    def resolve(self, name: str, version: int) -> HandlerFunc | None:
        return self._handlers.get((name, version))


# 全局 handler 注册表。
registry = HandlerRegistry()
