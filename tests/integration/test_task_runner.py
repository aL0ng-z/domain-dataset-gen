"""T07 runner 级测试（验收标准 1：持久队列 + 独立 runner 派发）。

覆盖：
- 创建 Task 后即使不经过 API 进程，独立 runner 仍能领取并完成（进程崩溃恢复）。
- runner 对未知 handler 永久失败（UNSUPPORTED_TASK_PAYLOAD），不无限重试。
- reaper 回收过期 processing 任务（lease 过期 -> 回队/取消/失败）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.project import Project
from app.models.task import Task, TaskAttempt
from app.workers.execution import ExecutionContext, HandlerRegistry
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


async def test_cancel_is_not_blocked_by_running_handler_and_rolls_back_business_write(
    db_session: AsyncSession,
    org,
    _test_session_factory: async_sessionmaker[AsyncSession],
):
    """handler 期间不持有 Task 行锁；取消可提交且 checkpoint 阻止业务发布。"""
    entered = asyncio.Event()
    release = asyncio.Event()
    published: list[uuid.UUID] = []
    project = org["projects"]["a"]
    original_description = project.description

    async def blocking_handler(ctx: ExecutionContext) -> None:
        await ctx.db.execute(
            update(Project)
            .where(Project.id == ctx.project_id)
            .values(description="不得发布的临时业务写入")
        )
        entered.set()
        await release.wait()
        await ctx.checkpoint()
        published.append(ctx.task_id)

    registry = HandlerRegistry()
    registry.register("test:cancel-unblocked", 1)(blocking_handler)
    await db_session.commit()
    task = await _make_task(
        db_session,
        handler="test:cancel-unblocked",
        project_id=project.id,
        created_by=org["users"]["admin"].id,
    )
    runner = TaskRunner(_test_session_factory, registry, worker_id="test-cancel-runner")
    # 预先签出第二条连接，避免 Windows/localhost 新建连接延迟干扰“行锁不阻塞”断言。
    async with _test_session_factory() as cancel_db:
        await cancel_db.execute(select(Task.id).where(Task.id == task.id))
        runner_call = asyncio.create_task(runner._claim_and_execute())
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            ok, status = await asyncio.wait_for(
                TaskQueue(cancel_db).request_cancel(
                    task_id=task.id,
                    cancel_requested_by=org["users"]["admin"].id,
                ),
                timeout=2,
            )
            await cancel_db.commit()
            assert ok is True
            assert status == "cancelling"
        finally:
            release.set()
        await asyncio.wait_for(runner_call, timeout=5)

    async with _test_session_factory() as verify_db:
        fresh_task = (
            await verify_db.execute(select(Task).where(Task.id == task.id))
        ).scalar_one()
        fresh_project = (
            await verify_db.execute(select(Project).where(Project.id == project.id))
        ).scalar_one()
        assert fresh_task.status == "cancelled"
        assert fresh_project.description == original_description
    assert published == []


async def test_heartbeat_and_lease_refresh_are_visible_to_other_sessions(
    db_session: AsyncSession,
    org,
    _test_session_factory: async_sessionmaker[AsyncSession],
):
    """心跳和按需 lease 刷新使用独立事务并立即对其他连接可见。"""
    await db_session.commit()
    task = await _make_task(
        db_session,
        handler="test:heartbeat",
        project_id=org["projects"]["a"].id,
        created_by=org["users"]["admin"].id,
    )
    claimed = await TaskQueue(db_session).claim_due(worker_id="heartbeat-worker", batch=1)
    await db_session.commit()
    task = claimed[0]
    old = datetime.now(UTC) - timedelta(minutes=5)
    async with _test_session_factory() as setup_db:
        await setup_db.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(heartbeat_at=old, lease_expires_at=datetime.now(UTC) + timedelta(seconds=60))
        )
        await setup_db.commit()

    ctx = ExecutionContext(
        task_id=task.id,
        run_token=task.run_token,
        project_id=task.project_id,
        attempt_no=task.attempt_count,
        worker_id="heartbeat-worker",
        payload={},
        payload_version=1,
        db=db_session,
        queue=TaskQueue(db_session),
        state_version=task.state_version,
        control_session_factory=_test_session_factory,
    )
    assert await ctx.heartbeat() is True
    async with _test_session_factory() as observer:
        after_heartbeat = (
            await observer.execute(select(Task).where(Task.id == task.id))
        ).scalar_one()
        assert after_heartbeat.heartbeat_at > old
        assert after_heartbeat.lease_expires_at > datetime.now(UTC)

    before_refresh = datetime.now(UTC)
    assert await ctx.refresh_lease(17) is True
    async with _test_session_factory() as observer:
        after_refresh = (
            await observer.execute(select(Task).where(Task.id == task.id))
        ).scalar_one()
        assert after_refresh.lease_expires_at >= before_refresh + timedelta(seconds=16)


async def test_retryable_failure_runs_terminal_hook_only_after_attempts_exhausted(
    db_session: AsyncSession,
    org,
    _test_session_factory: async_sessionmaker[AsyncSession],
):
    """自动回队不是业务终态；只有额度耗尽后才运行 terminal hook。"""
    hook_statuses: list[str] = []

    async def failing_handler(ctx: ExecutionContext) -> None:
        async def terminal_hook(_db: AsyncSession, status: str, _message: str | None) -> None:
            hook_statuses.append(status)

        ctx.set_terminal_hook(terminal_hook)
        raise TimeoutError("transient")

    registry = HandlerRegistry()
    registry.register("test:retry-terminal", 1)(failing_handler)
    await db_session.commit()
    task = await _make_task(
        db_session,
        handler="test:retry-terminal",
        project_id=org["projects"]["a"].id,
        created_by=org["users"]["admin"].id,
        max_attempts=2,
    )
    runner = TaskRunner(_test_session_factory, registry, worker_id="test-retry-runner")

    await runner._claim_and_execute()
    async with _test_session_factory() as verify_db:
        first = (
            await verify_db.execute(select(Task).where(Task.id == task.id))
        ).scalar_one()
        assert first.status == "queued"
        assert first.attempt_count == 1
    assert hook_statuses == []

    async with _test_session_factory() as due_db:
        await due_db.execute(
            update(Task).where(Task.id == task.id).values(next_run_at=datetime.now(UTC))
        )
        await due_db.commit()
    await runner._claim_and_execute()

    async with _test_session_factory() as verify_db:
        exhausted = (
            await verify_db.execute(select(Task).where(Task.id == task.id))
        ).scalar_one()
        assert exhausted.status == "failed"
        assert exhausted.attempt_count == 2
    assert hook_statuses == ["failed"]
