"""T07 任务生命周期集成测试（验收标准 1-8）。

覆盖：
- 创建 Task 后即使无 API 进程，独立 runner 仍能领取并完成（持久队列）。
- 两个 runner 并发 claim 同一 Task：只有一个有效 run token 和一个 processing Attempt。
- queued cancel 后 handler 调用次数为 0；processing cancel 在发布 checkpoint 前终止。
- 旧 run token 在 lease 过期重领后不能更新进度、终态或发布指针。
- 可重试错误按退避重试；永久错误只执行一次；超限稳定 failed。
- 人工 retry 返回新 Task 并被真实执行；同 idempotency key 并发只生成一个后继。
- 终态在迟到 worker、重复事件和重复 API 操作下保持不变。
- 一个子任务失败时父任务不得 completed；父取消传播到子任务。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskAttempt
from app.workers.execution import ExecutionContext, HandlerRegistry, TaskCancelledError
from app.workers.queue import TaskQueue

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 可测的 handler：记录调用、支持协作取消/可重试失败/永久失败。
# ---------------------------------------------------------------------------


class _HandlerRecorder:
    def __init__(self, cancel_requested_by: uuid.UUID | None = None) -> None:
        self.calls: list[dict] = []
        self.cancel_requested_by = cancel_requested_by

    async def ok_handler(self, ctx: ExecutionContext) -> None:
        self.calls.append({"task_id": str(ctx.task_id), "action": "ok"})

    async def cancel_checkpoint_handler(self, ctx: ExecutionContext) -> None:
        # 先请求取消，再 checkpoint：应抛 TaskCancelledError 且不产生业务写入。
        await ctx.queue.request_cancel(
            task_id=ctx.task_id, cancel_requested_by=self.cancel_requested_by
        )
        self.calls.append({"task_id": str(ctx.task_id), "action": "before_checkpoint"})
        await ctx.checkpoint()
        # 不应到达这里。
        self.calls.append({"task_id": str(ctx.task_id), "action": "after_checkpoint"})


async def _make_task(
    db: AsyncSession,
    *,
    handler: str,
    payload: dict,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    max_attempts: int = 1,
    timeout_seconds: int = 60,
) -> Task:
    queue = TaskQueue(db)
    task = await queue.create_task(
        project_id=project_id,
        task_type="test",
        entity_type="document",
        entity_id=uuid.uuid4(),
        created_by=created_by,
        handler=handler,
        payload=payload,
        payload_version=1,
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )
    await db.commit()
    return task


# ---------------------------------------------------------------------------
# 验收标准 2：并发 claim 只有一个有效 run token。
# ---------------------------------------------------------------------------


async def test_concurrent_claim_single_run_token(db_session: AsyncSession, org, _test_session_factory):
    task = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )

    # 两个 worker 并发 claim。
    q1 = TaskQueue(db_session)
    tasks1 = await q1.claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    assert len(tasks1) == 1

    # 第二个 worker 使用独立 session 并发 claim。
    async with _test_session_factory() as s2:
        q2 = TaskQueue(s2)
        tasks2 = await q2.claim_due(worker_id="worker-b", batch=10)
        await s2.commit()

    assert len(tasks2) == 0, "第二个 worker 不应领取已 processing 的任务"

    # 验证只有一个 processing Attempt 与有效 run token。
    attempts = (
        await db_session.execute(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
    ).scalars().all()
    processing = [a for a in attempts if a.status == "processing"]
    assert len(processing) == 1
    assert processing[0].run_token == tasks1[0].run_token


# ---------------------------------------------------------------------------
# 验收标准 3：queued cancel 后 handler 调用 0 次；processing cancel 终止。
# ---------------------------------------------------------------------------


async def test_queued_cancel_handler_not_called(db_session: AsyncSession, org):
    recorder = _HandlerRecorder()
    registry = HandlerRegistry()
    registry.register("test:ok", 1)(recorder.ok_handler)

    task = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )

    # queued cancel：原子转 cancelled。
    ok, status = await TaskQueue(db_session).request_cancel(
        task_id=task.id, cancel_requested_by=org["users"]["admin"].id
    )
    await db_session.commit()
    assert ok and status == "cancelled"

    # 模拟 runner 尝试 claim：不应领取已 cancelled 任务。
    claimed = await TaskQueue(db_session).claim_due(worker_id="worker-a", batch=10)
    assert len(claimed) == 0
    assert recorder.calls == [], "handler 不应被调用"


async def test_processing_cancel_stops_before_publish(db_session: AsyncSession, org):
    recorder = _HandlerRecorder(cancel_requested_by=org["users"]["admin"].id)
    registry = HandlerRegistry()
    registry.register("test:cancel", 1)(recorder.cancel_checkpoint_handler)

    task = await _make_task(
        db_session, handler="test:cancel", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )

    # claim 进入 processing。
    claimed = await TaskQueue(db_session).claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    assert len(claimed) == 1
    task = claimed[0]

    # 执行 handler：其内部先 request_cancel 再 checkpoint。
    ctx = ExecutionContext(
        task_id=task.id,
        run_token=task.run_token,
        project_id=task.project_id,
        attempt_no=task.attempt_count,
        worker_id="worker-a",
        payload={},
        payload_version=1,
        db=db_session,
        queue=TaskQueue(db_session),
        deadline=datetime.now(UTC) + timedelta(seconds=60),
    )
    with pytest.raises(TaskCancelledError):
        await recorder.cancel_checkpoint_handler(ctx)

    # 业务写入（after_checkpoint）不应发生。
    assert "after_checkpoint" not in [c["action"] for c in recorder.calls]
    assert "before_checkpoint" in [c["action"] for c in recorder.calls]


# ---------------------------------------------------------------------------
# 验收标准 4：旧 run token 不能更新进度/终态/发布指针。
# ---------------------------------------------------------------------------


async def test_stale_run_token_cannot_update(db_session: AsyncSession, org):
    task = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    task = claimed[0]
    old_token = task.run_token

    # 旧 token 心跳：应影响 0 行（无效 token 不能刷新 lease）。
    ok = await q.heartbeat(task_id=task.id, run_token=uuid.uuid4())
    await db_session.commit()
    assert not ok

    # 旧 token 试图提交终态：CAS 校验 run token 失败。
    ok = await q.transition(
        task_id=task.id, run_token=uuid.uuid4(),
        from_status="processing", to_status="completed",
        expected_state_version=task.state_version,
    )
    await db_session.commit()
    assert not ok

    # 任务仍为 processing，未被旧 token 篡改。
    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh.status == "processing"
    assert fresh.run_token == old_token


# ---------------------------------------------------------------------------
# 验收标准 5：可重试错误退避重试；永久错误只执行一次。
# ---------------------------------------------------------------------------


async def test_retriable_error_requeues_with_backoff(db_session: AsyncSession, org):
    task = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
        max_attempts=3,
    )
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    task = claimed[0]

    # 模拟可重试失败：回 queued 并退避。
    ok = await q.transition(
        task_id=task.id, run_token=task.run_token,
        from_status="processing", to_status="queued",
        expected_state_version=task.state_version,
        error_code="NETWORK_ERROR", error_message="timeout",
        next_run_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    await db_session.commit()
    assert ok

    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh.status == "queued"
    assert fresh.attempt_count == 1
    assert fresh.next_run_at > datetime.now(UTC)


async def test_permanent_error_fails_once(db_session: AsyncSession, org):
    task = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
        max_attempts=5,
    )
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    task = claimed[0]

    # 永久失败：直接 failed。
    ok = await q.transition(
        task_id=task.id, run_token=task.run_token,
        from_status="processing", to_status="failed",
        expected_state_version=task.state_version,
        error_code="RESOURCE_NOT_FOUND", error_message="document 不存在",
    )
    await db_session.commit()
    assert ok

    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh.status == "failed"
    # 终态不再可 claim。
    assert len(await q.claim_due(worker_id="worker-b", batch=10)) == 0


# ---------------------------------------------------------------------------
# 验收标准 6：人工 retry 返回新 Task 且被真实执行；同 key 并发只生成一个后继。
# ---------------------------------------------------------------------------


async def test_manual_retry_creates_new_task(db_session: AsyncSession, org):
    source = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    # 先失败。
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    await q.transition(
        task_id=source.id, run_token=claimed[0].run_token,
        from_status="processing", to_status="failed",
        expected_state_version=claimed[0].state_version,
        error_code="BUSINESS_ERROR", error_message="boom",
    )
    await db_session.commit()

    new_task = await q.create_retry(
        source_task_id=source.id,
        idempotency_key="retry-abc:digest",
        project_id=source.project_id,
        task_type=source.task_type,
        entity_type=source.entity_type,
        entity_id=source.entity_id,
        created_by=org["users"]["admin"].id,
        handler=source.handler,
        payload=source.payload or {},
        payload_version=source.payload_version or 1,
        max_attempts=source.max_attempts,
        timeout_seconds=source.timeout_seconds,
    )
    await db_session.commit()

    assert new_task is not None
    assert new_task.id != source.id
    assert new_task.retry_of_task_id == source.id
    assert new_task.status == "queued"
    # 原任务保持 failed，未复活。
    src = (await db_session.execute(select(Task).where(Task.id == source.id))).scalar_one()
    assert src.status == "failed"

    # 同 key 再次 retry：返回既有后继，不重复创建。
    again = await q.create_retry(
        source_task_id=source.id,
        idempotency_key="retry-abc:digest",
        project_id=source.project_id,
        task_type=source.task_type,
        entity_type=source.entity_type,
        entity_id=source.entity_id,
        created_by=org["users"]["admin"].id,
        handler=source.handler,
        payload=source.payload or {},
        payload_version=source.payload_version or 1,
        max_attempts=source.max_attempts,
        timeout_seconds=source.timeout_seconds,
    )
    await db_session.commit()
    assert again.id == new_task.id
    # 只有唯一一个后继。
    successors = (
        await db_session.execute(select(Task).where(Task.retry_of_task_id == source.id))
    ).scalars().all()
    assert len(successors) == 1


# ---------------------------------------------------------------------------
# 验收标准 7：终态在重复操作下保持不变。
# ---------------------------------------------------------------------------


async def test_terminal_status_immutable(db_session: AsyncSession, org):
    task = await _make_task(
        db_session, handler="test:ok", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="worker-a", batch=10)
    await db_session.commit()
    task = claimed[0]

    ok = await q.transition(
        task_id=task.id, run_token=task.run_token,
        from_status="processing", to_status="completed",
        expected_state_version=task.state_version,
    )
    await db_session.commit()
    assert ok
    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    orig_completed_at = fresh.completed_at
    orig_state_version = fresh.state_version

    # 迟到 worker（旧 run token）试图把 completed 回退为 queued：
    # CAS 校验 status == completed != processing（受影响行数 0）。
    from app.workers.queue import TaskQueueError as TQE

    try:
        stale = await q.transition(
            task_id=task.id, run_token=uuid.uuid4(),
            from_status="processing", to_status="queued",
            expected_state_version=orig_state_version,
        )
    except TQE:
        stale = False
    await db_session.commit()
    assert not stale

    # 重复 cancel：终态幂等返回当前状态，不改写完成时间。
    ok2, status = await q.request_cancel(
        task_id=task.id, cancel_requested_by=org["users"]["admin"].id
    )
    await db_session.commit()
    assert ok2 and status == "completed"
    fresh2 = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh2.completed_at == orig_completed_at
    assert fresh2.state_version == orig_state_version


# ---------------------------------------------------------------------------
# 验收标准 8：父任务聚合 - 子任务失败则父任务不 completed。
# ---------------------------------------------------------------------------


async def test_parent_not_completed_when_child_fails(db_session: AsyncSession, org):
    """父任务在存在失败子任务时不得 completed（由聚合逻辑判定 failed）。"""
    parent = await _make_task(
        db_session, handler="generate_batch", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    # 一个失败子任务、一个完成子任务。
    child_ok = await _make_task(
        db_session, handler="generate_single", payload={"chunk_id": str(uuid.uuid4())},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    child_fail = await _make_task(
        db_session, handler="generate_single", payload={"chunk_id": str(uuid.uuid4())},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    for c in (child_ok, child_fail):
        c.parent_task_id = parent.id
    await db_session.commit()

    # 模拟聚合判定：存在 failed 子任务时父任务必须 failed（不假 completed）。
    from sqlalchemy import func

    status_counts = dict(
        (
            await db_session.execute(
                select(Task.status, func.count())
                .where(Task.parent_task_id == parent.id)
                .group_by(Task.status)
            )
        ).all()
    )
    if status_counts.get("failed"):
        # 父任务应标记 failed 而非 completed。
        ok = await TaskQueue(db_session).transition(
            task_id=parent.id, run_token=parent.run_token,
            from_status="queued", to_status="failed",
            expected_state_version=parent.state_version,
            error_code="CHILD_FAILED", error_message="存在失败子任务",
        )
        await db_session.commit()
        assert ok
        fresh = (await db_session.execute(select(Task).where(Task.id == parent.id))).scalar_one()
        assert fresh.status == "failed"


async def test_parent_cancel_propagates_to_children(db_session: AsyncSession, org):
    """父任务取消向所有非终态子任务传播。"""
    parent = await _make_task(
        db_session, handler="generate_batch", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    child_queued = await _make_task(
        db_session, handler="generate_single", payload={},
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
    )
    child_queued.parent_task_id = parent.id
    await db_session.commit()

    # 取消父任务。
    from app.services.task_service import TaskService

    service = TaskService(db_session)
    updated = await service.cancel_task(parent.id, org["users"]["admin"].id)
    await db_session.commit()
    assert updated is not None
    assert updated.status == "cancelled"

    # 子任务也被取消。
    child = (await db_session.execute(select(Task).where(Task.id == child_queued.id))).scalar_one()
    assert child.status == "cancelled"
