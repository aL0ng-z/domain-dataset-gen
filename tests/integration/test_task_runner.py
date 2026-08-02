"""T07 runner 级测试（验收标准 1：持久队列 + 独立 runner 派发）。

覆盖：
- 创建 Task 后即使不经过 API 进程，独立 runner 仍能领取并完成（进程崩溃恢复）。
- runner 对未知 handler 永久失败（UNSUPPORTED_TASK_PAYLOAD），不无限重试。
- reaper 回收过期 processing 任务（lease 过期 -> 回队/取消/失败）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task import Task, TaskAttempt
from app.workers.execution import ExecutionContext
from app.workers.queue import TaskQueue
from app.workers.runner import TaskRunner

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 可测 handler：正常完成 / 可重试失败 / 永久失败。
# ---------------------------------------------------------------------------


class _ExecRecorder:
    def __init__(self) -> None:
        self.completed: list[uuid.UUID] = []

    async def ok_handler(self, ctx: ExecutionContext) -> None:
        # 模拟业务写入 + 发布门禁。
        await ctx.checkpoint()
        self.completed.append(ctx.task_id)

    async def retryable_handler(self, ctx: ExecutionContext) -> None:
        raise TimeoutError("network timeout")


async def _make_task(
    db: AsyncSession,
    *,
    handler: str,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    max_attempts: int = 1,
    timeout_seconds: int = 60,
) -> Task:
    task = await TaskQueue(db).create_task(
        project_id=project_id,
        task_type="test",
        entity_type="document",
        entity_id=uuid.uuid4(),
        created_by=created_by,
        handler=handler,
        payload={},
        payload_version=1,
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )
    await db.commit()
    return task


async def test_runner_picks_up_persisted_task(
    db_session: AsyncSession,
    org,
    _test_session_factory: async_sessionmaker[AsyncSession],
):
    """验收标准 1：创建 Task 后（模拟 API 进程已终止）runner 仍能领取并完成。"""
    from app.workers.execution import HandlerRegistry

    recorder = _ExecRecorder()
    registry = HandlerRegistry()
    registry.register("test:runner-ok", 1)(recorder.ok_handler)

    # 直接创建 Task（不经 API 路由；runner 独立进程语义）。
    # 先提交 org 数据，保证 runner 独立会话可见 project/user（FK 约束）。
    await db_session.commit()
    task = await _make_task(
        db_session, handler="test:runner-ok",
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )

    # 启动 runner 单轮（进程崩溃恢复后重新派发）。
    runner = TaskRunner(_test_session_factory, registry, worker_id="test-runner-1")
    await runner._claim_and_execute()

    # runner 在独立会话执行；用全新会话读取最终状态（避免 identity map 陈旧）。
    async with _test_session_factory() as s:
        fresh = (await s.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert fresh.status == "completed"
        assert fresh.progress == 100
        assert fresh.completed_at is not None
        assert recorder.completed == [task.id]
        # Attempt 审计完成。
        attempts = (
            await s.execute(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
        ).scalars().all()
        assert attempts[0].status == "completed"


async def test_runner_permanent_fail_on_unknown_handler(
    db_session: AsyncSession,
    org,
    _test_session_factory: async_sessionmaker[AsyncSession],
):
    """未知 handler/version -> 永久失败，不无限重试。"""
    from app.workers.execution import HandlerRegistry

    registry = HandlerRegistry()
    # 故意不注册 test:unknown。
    await db_session.commit()
    task = await _make_task(
        db_session, handler="test:unknown",
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
        max_attempts=5,
    )

    runner = TaskRunner(_test_session_factory, registry, worker_id="test-runner-2")
    await runner._claim_and_execute()

    async with _test_session_factory() as s:
        fresh = (await s.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert fresh.status == "failed"
        assert fresh.error_code == "UNSUPPORTED_TASK_PAYLOAD"
        # 只尝试一次，不因 max_attempts=5 无限重试。
        assert fresh.attempt_count == 1


async def test_reaper_reclaims_expired_processing(
    db_session: AsyncSession,
    org,
):
    """reaper 回收 lease 过期的 processing 任务（可重试 -> 回队退避）。"""
    task = await _make_task(
        db_session, handler="test:reaper",
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
        max_attempts=3,
    )
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="dead-worker", batch=10)
    await db_session.commit()
    task = claimed[0]
    # 模拟 worker 崩溃前已记录可重试错误（网络失败），随后 lease 过期。
    task.error_code = "NETWORK_ERROR"
    # 人为让 lease 过期（把 lease_expires_at 改到过去）。
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=300)
    await db_session.commit()

    stats = await q.reap_expired(lease_grace_seconds=0, batch=50)
    await db_session.commit()

    # 可重试且未超限：回队退避。
    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh.status == "queued"
    assert fresh.next_run_at > datetime.now(UTC)
    assert stats["requeued"] == 1


async def test_reaper_cancels_task_with_cancel_request(
    db_session: AsyncSession,
    org,
):
    """reaper 对过期且带取消请求的任务直接转 cancelled。"""
    task = await _make_task(
        db_session, handler="test:reaper-cancel",
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="dead-worker", batch=10)
    await db_session.commit()
    task = claimed[0]
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=300)
    await db_session.commit()

    # 请求取消（processing -> cancelling）。
    await q.request_cancel(task_id=task.id, cancel_requested_by=org["users"]["admin"].id)
    await db_session.commit()

    stats = await q.reap_expired(lease_grace_seconds=0, batch=50)
    await db_session.commit()

    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh.status == "cancelled"
    assert stats["cancelled"] == 1
