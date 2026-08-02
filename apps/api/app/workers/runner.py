"""独立任务 runner（T07 §4.3、§5）。

runner 是独立长期进程：轮询 PostgreSQL 队列领取到期 Task，分派到对应 handler
执行，心跳/检查点由 ExecutionContext 提供，异常分类落库后再确认 attempt。
worker 崩溃后由 reaper 回收 lease，Task 可恢复执行。

至少一次执行 + 幂等 handler：不虚假承诺 exactly-once。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task import Task
from app.workers.errors import TaskErrorCode
from app.workers.execution import (
    ExecutionContext,
    HandlerRegistry,
    TaskCancelledError,
    TaskProtocolError,
    TaskTimeoutError,
)
from app.workers.queue import TaskQueue

logger = logging.getLogger(__name__)

# 心跳间隔（秒）。
HEARTBEAT_INTERVAL_SECONDS = 30
# 领取批次大小。
CLAIM_BATCH = 5
# 轮询空队列的退避（秒）。
IDLE_POLL_INTERVAL_SECONDS = 2.0
# reaper 回收间隔（秒）。
REAP_INTERVAL_SECONDS = 30.0


def _now() -> datetime:
    return datetime.now(UTC)


def classify_error(exc: Exception) -> tuple[TaskErrorCode, bool]:
    """把 handler 异常分类为稳定错误码 + 是否可重试。

    可重试：网络/限流/临时基础设施。永久：校验/资源缺失/合同/未知 payload。
    默认（未知异常）按永久失败处理——避免无限重试掩盖真实 bug。
    """
    # 已知异常类型 -> 可重试。
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return TaskErrorCode.NETWORK_ERROR, True
    # 明确限流。
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "throttl" in name:
        return TaskErrorCode.RATE_LIMITED, True
    # 数据库临时故障可重试。
    if isinstance(exc, SQLAlchemyError):
        return TaskErrorCode.TEMPORARY_INFRA_ERROR, True
    # 默认永久失败。
    return TaskErrorCode.BUSINESS_ERROR, False


@dataclasses.dataclass
class _Outcome:
    """单次 handler 执行结果（失败/取消路径在业务写入回滚后单独落库）。"""

    status: str  # completed / failed / cancelled / requeued
    retriable: bool = False
    error_code: TaskErrorCode | None = None
    error: str | None = None
    requeue_after: int | None = None


class TaskRunner:
    """轮询队列、分派 handler、管理 attempt 生命周期的 runner。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        registry: HandlerRegistry,
        *,
        worker_id: str | None = None,
    ):
        self.session_factory = session_factory
        self.registry = registry
        self.worker_id = worker_id or f"runner-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """主循环：claim -> execute -> reap，直到收到停止信号。"""
        logger.info("runner %s 启动", self.worker_id)
        last_reap = _now()
        while not self._stop.is_set():
            try:
                await self._claim_and_execute()
            except Exception:  # noqa: BLE001 - 主循环不因单轮异常退出
                logger.exception("runner 单轮执行异常，继续轮询")
                await asyncio.sleep(IDLE_POLL_INTERVAL_SECONDS)

            if _now() - last_reap >= timedelta(seconds=REAP_INTERVAL_SECONDS):
                try:
                    await self._reap()
                except Exception:  # noqa: BLE001
                    logger.exception("reaper 异常")
                last_reap = _now()

            if self._stop.is_set():
                break
            # 避免空转：本轮没有任务时稍作退避。
            await asyncio.sleep(IDLE_POLL_INTERVAL_SECONDS)
        logger.info("runner %s 已停止", self.worker_id)

    async def _claim_and_execute(self) -> None:
        # claim 在独立事务提交；每个任务在执行时再打开自己的会话。
        async with self.session_factory() as session:
            queue = TaskQueue(session)
            try:
                tasks = await queue.claim_due(worker_id=self.worker_id, batch=CLAIM_BATCH)
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()
                return
        for task in tasks:
            await self._execute_one(task)

    async def _execute_one(self, task: Task) -> None:
        """执行单个任务并确认 attempt。

        成功路径：业务写入 + completed 转换在同一事务内提交（发布事务）。
        失败/取消路径：回滚全部业务写入，再在全新事务中写入失败/取消状态，
        确保取消或失败前不得留下正式业务产物（验收标准 3）。
        """
        run_token = task.run_token
        attempt_no = task.attempt_count
        async with self.session_factory() as session:
            queue = TaskQueue(session)
            try:
                # 加锁读取（claim 已提交，可能被 reaper 改动）。
                fresh = (
                    await session.execute(select(Task).where(Task.id == task.id).with_for_update())
                ).scalar_one_or_none()
                if fresh is None or fresh.run_token != run_token or fresh.status != "processing":
                    return
                task = fresh

                handler = self.registry.resolve(task.handler, task.payload_version or 1)
                if handler is None:
                    # 未知 handler/version：永久失败（无业务写入，同事务即可）。
                    await self._fail_permanent(
                        queue, task, run_token, TaskErrorCode.UNSUPPORTED_TASK_PAYLOAD,
                        f"未知 handler/version: {task.handler}:v{task.payload_version or 1}",
                    )
                    await session.commit()
                    return

                ctx = ExecutionContext(
                    task_id=task.id,
                    run_token=run_token,
                    project_id=task.project_id,
                    attempt_no=attempt_no,
                    worker_id=self.worker_id,
                    payload=task.payload or {},
                    payload_version=task.payload_version or 1,
                    db=session,
                    queue=queue,
                    deadline=_now() + timedelta(seconds=max(1, task.timeout_seconds)),
                )

                outcome: _Outcome
                try:
                    await handler(ctx)
                    # 发布事务前门禁：校验 run token + cancel。
                    await ctx.checkpoint()
                    if ctx.requeue_after is not None:
                        # 父任务聚合：回队退避轮询，不标 completed。
                        outcome = _Outcome(
                            status="requeued", requeue_after=ctx.requeue_after,
                        )
                    else:
                        outcome = _Outcome(status="completed")
                except TaskCancelledError:
                    outcome = _Outcome(status="cancelled")
                except TaskTimeoutError:
                    outcome = _Outcome(
                        status="failed", retriable=True,
                        error_code=TaskErrorCode.TEMPORARY_INFRA_ERROR, error="任务执行超时",
                    )
                except TaskProtocolError as exc:
                    # run token 失效：不覆盖任何状态，直接放弃本 attempt。
                    logger.warning("task %s run token 失效：%s", task.id, exc)
                    await session.rollback()
                    return
                except Exception as exc:  # noqa: BLE001
                    error_code, retriable = classify_error(exc)
                    outcome = _Outcome(
                        status="failed", retriable=retriable,
                        error_code=error_code, error=str(exc),
                    )

                if outcome.status == "completed":
                    ok = await queue.transition(
                        task_id=task.id, run_token=run_token,
                        from_status="processing", to_status="completed",
                        expected_state_version=task.state_version,
                    )
                    if ok:
                        await queue.finish_attempt(
                            task_id=task.id, run_token=run_token, attempt_no=attempt_no,
                            status="completed",
                        )
                        # 业务写入 + 完成转换原子提交。
                        await session.commit()
                    else:
                        await session.rollback()
                    return

                if outcome.status == "requeued":
                    # 回队退避：业务写入（子任务创建）与 queued 转换原子提交。
                    delay = outcome.requeue_after or 10
                    ok = await queue.transition(
                        task_id=task.id, run_token=run_token,
                        from_status="processing", to_status="queued",
                        expected_state_version=task.state_version,
                        next_run_at=_now() + timedelta(seconds=delay),
                    )
                    if ok:
                        await queue.finish_attempt(
                            task_id=task.id, run_token=run_token, attempt_no=attempt_no,
                            status="completed",
                        )
                        await session.commit()
                    else:
                        await session.rollback()
                    return

                # 失败/取消：先回滚全部业务写入。
                await session.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("执行 task %s 时发生未预期异常", task.id)
                await session.rollback()
                return

        # 在全新事务中写入失败/取消状态（业务写入已回滚）。
        await self._apply_terminal_outcome(task.id, run_token, attempt_no, outcome, task.state_version)

    async def _apply_terminal_outcome(
        self, task_id: uuid.UUID, run_token: uuid.UUID, attempt_no: int,
        outcome: _Outcome, expected_state_version: int,
    ) -> None:
        """在全新会话中应用失败/取消状态转换。"""
        async with self.session_factory() as session:
            queue = TaskQueue(session)
            try:
                # 重新读取任务（可能已被 reaper 改动），以最新 state_version 做 CAS。
                current = (
                    await session.execute(select(Task).where(Task.id == task_id).with_for_update())
                ).scalar_one_or_none()
                if current is None or current.run_token != run_token:
                    await session.rollback()
                    return
                if outcome.status == "cancelled":
                    # cancel 请求已由 request_cancel 将 processing 置为 cancelling；
                    # 但可能存在竞态（checkpoint 读到 cancel 标志时状态仍为 processing），
                    # 两种来源都按状态图收敛到 cancelled。
                    if current.status == "cancelling":
                        await queue.transition(
                            task_id=task_id, run_token=run_token,
                            from_status="cancelling", to_status="cancelled",
                            expected_state_version=current.state_version,
                        )
                    else:
                        await queue.transition(
                            task_id=task_id, run_token=run_token,
                            from_status="processing", to_status="cancelling",
                            expected_state_version=current.state_version,
                        )
                        await queue.transition(
                            task_id=task_id, run_token=run_token,
                            from_status="cancelling", to_status="cancelled",
                            expected_state_version=current.state_version + 1,
                        )
                    await queue.finish_attempt(
                        task_id=task_id, run_token=run_token, attempt_no=attempt_no,
                        status="cancelled", error_code=TaskErrorCode.TASK_CANCELLED.value,
                        error_message="任务已请求取消",
                    )
                else:
                    await self._handle_failure(
                        queue, current, run_token, attempt_no,
                        outcome.error_code, outcome.error, retriable=outcome.retriable,
                    )
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()

    async def _fail_permanent(
        self, queue: TaskQueue, task: Task, run_token: uuid.UUID,
        error_code: TaskErrorCode, message: str,
    ) -> None:
        ok = await queue.transition(
            task_id=task.id, run_token=run_token,
            from_status="processing", to_status="failed",
            expected_state_version=task.state_version,
            error_code=error_code.value, error_message=message,
        )
        if ok:
            await queue.finish_attempt(
                task_id=task.id, run_token=run_token, attempt_no=task.attempt_count,
                status="failed", error_code=error_code.value, error_message=message,
            )

    async def _handle_failure(
        self, queue: TaskQueue, task: Task, run_token: uuid.UUID, attempt_no: int,
        error_code: TaskErrorCode, message: str, *, retriable: bool,
    ) -> None:
        """按错误分类决定：可重试且有额度 -> 回 queued 退避；否则 failed。"""
        if retriable and task.attempt_count < task.max_attempts:
            from app.workers.queue import _backoff_delay

            delay = _backoff_delay(task.attempt_count)
            ok = await queue.transition(
                task_id=task.id, run_token=run_token,
                from_status="processing", to_status="queued",
                expected_state_version=task.state_version,
                error_message=message, error_code=error_code.value,
                next_run_at=_now() + timedelta(seconds=delay),
            )
        else:
            ok = await queue.transition(
                task_id=task.id, run_token=run_token,
                from_status="processing", to_status="failed",
                expected_state_version=task.state_version,
                error_message=message, error_code=error_code.value,
            )

        if ok:
            await queue.finish_attempt(
                task_id=task.id, run_token=run_token, attempt_no=attempt_no,
                status="failed", error_code=error_code.value,
                error_message=message, retriable=retriable,
            )
        else:
            # CAS 失败（状态已被他人改变）：attempt 标记 abandoned，不覆盖。
            await queue.finish_attempt(
                task_id=task.id, run_token=run_token, attempt_no=attempt_no,
                status="abandoned", error_code=error_code.value, error_message=message,
            )

    async def _reap(self) -> None:
        async with self.session_factory() as session:
            queue = TaskQueue(session)
            try:
                stats = await queue.reap_expired()
                await session.commit()
                if any(stats.values()):
                    logger.info("reaper 回收：%s", stats)
            except Exception:  # noqa: BLE001
                await session.rollback()


# ---------------------------------------------------------------------------
# 启动入口：`python -m app.workers.runner`
# ---------------------------------------------------------------------------


async def _main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings

    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 注册业务 handler（延迟导入避免循环依赖）。
    from app.workers import register_all_handlers
    from app.workers.execution import registry

    register_all_handlers()

    runner = TaskRunner(session_factory, registry)
    try:
        await runner.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
