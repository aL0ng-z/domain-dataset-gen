"""任务队列原子原语（T07 §4.3）：create / claim / heartbeat / CAS transition / reaper。

所有转换使用数据库 compare-and-set（CAS）：``UPDATE ... WHERE id=? AND status=?
AND state_version=?``，受影响行数不是 1 即视为竞态失败。runner 用
``SELECT ... FOR UPDATE SKIP LOCKED`` 领取到期 queued Task，递增 attempt，
生成 run token/lease，并在同一事务内创建 Attempt。

worker 心跳和所有状态提交必须携带 run token；过期 worker 的提交影响 0 行，
不能覆盖新 attempt 或终态（验收标准 4）。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.config import TaskPolicy
from app.models.task import Task, TaskAttempt
from app.workers.errors import TaskErrorCode, is_retriable

logger = logging.getLogger(__name__)

# 合法转换表（任务卡 §4.1）。
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"processing", "cancelled"}),
    "processing": frozenset({"queued", "completed", "failed", "cancelling"}),
    "cancelling": frozenset({"cancelled"}),
}

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "processing", "cancelling"})


class TaskQueueError(RuntimeError):
    """队列原子操作竞态/非法转换错误。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _backoff_delay(attempt_count: int, base_seconds: int = 5, max_seconds: int = 300) -> int:
    """指数退避：attempt 1 -> 5s, 2 -> 10s, ..., 封顶 300s。"""
    return min(base_seconds * (2 ** max(attempt_count - 1, 0)), max_seconds)


class TaskQueue:
    """PostgreSQL 持久任务队列核心。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        project_id: uuid.UUID,
        task_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        created_by: uuid.UUID,
        handler: str,
        payload: dict[str, Any],
        payload_version: int = 1,
        parent_task_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 1,
        timeout_seconds: int = 300,
        next_run_at: datetime | None = None,
        policy_snapshot: dict[str, Any] | None = None,
        resolve_policy: bool = True,
    ) -> Task:
        """在调用方事务内创建 Task（不自行 commit）。"""
        if resolve_policy:
            policy = await self.db.scalar(select(TaskPolicy).where(
                TaskPolicy.project_id == project_id, TaskPolicy.task_type == task_type,
                TaskPolicy.is_default.is_(True),
            ).execution_options(populate_existing=True))
            if policy is not None:
                max_attempts = policy.max_retries + 1
                timeout_seconds = policy.timeout_seconds
                policy_snapshot = {
                    "id": str(policy.id), "version": policy.version, "task_type": task_type,
                    "max_retries": policy.max_retries, "timeout_seconds": timeout_seconds,
                }
        if policy_snapshot is None:
            policy_snapshot = {"id": None, "task_type": task_type,
                               "max_retries": max_attempts - 1, "timeout_seconds": timeout_seconds}
        task = Task(
            project_id=project_id,
            task_type=task_type,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by=created_by,
            handler=handler,
            payload=payload,
            payload_version=payload_version,
            parent_task_id=parent_task_id,
            idempotency_key=idempotency_key,
            status="queued",
            state_version=0,
            progress=0,
            policy_snapshot=policy_snapshot,
            max_attempts=max(1, max_attempts),
            timeout_seconds=max(1, timeout_seconds),
            next_run_at=next_run_at or _now(),
            is_legacy=False,
        )
        self.db.add(task)
        await self.db.flush()
        await self.db.refresh(task)
        from app.workers.lifecycle import bind_document_job
        await bind_document_job(self.db, task)
        return task

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    async def claim_due(self, *, worker_id: str, batch: int = 1) -> list[Task]:
        """领取到期 queued Task，返回已带 run token/lease 的任务列表。

        用 ``SELECT ... FOR UPDATE SKIP LOCKED`` 避免多 runner 并发领取同一任务；
        claim 与 Attempt 创建在同一事务内（调用方负责 commit）。
        """
        # Filter already saturated groups in SQL so an early backlog cannot hide
        # runnable work in other projects/types beyond the bounded candidate window.
        now = _now()
        active_task = aliased(Task)
        active_count = select(func.count()).select_from(active_task).where(
            active_task.project_id == TaskPolicy.project_id,
            active_task.task_type == TaskPolicy.task_type,
            active_task.status.in_(("processing", "cancelling")),
            active_task.lease_expires_at > now,
        ).correlate(TaskPolicy).scalar_subquery()
        saturated = select(TaskPolicy.id).where(
            TaskPolicy.project_id == Task.project_id, TaskPolicy.task_type == Task.task_type,
            TaskPolicy.is_default.is_(True), TaskPolicy.concurrency_limit <= active_count,
        ).correlate(Task).exists()
        stmt = (
            select(Task)
            .where(
                Task.status == "queued",
                Task.next_run_at <= now,
                ~saturated,
            )
            .order_by(Task.next_run_at)
            .limit(max(100, batch * 20))
            .with_for_update(skip_locked=True)
        )
        result = await self.db.execute(stmt)
        tasks = list(result.scalars().all())
        if not tasks:
            return []

        claimed: list[Task] = []
        for task in tasks:
            if len(claimed) >= batch:
                break
            # A transaction-scoped policy lock serializes count+claim across runners.
            # Try-lock avoids deadlocking against another runner holding candidate rows.
            locked = await self.db.scalar(text(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"
            ), {"key": f"task-policy:{task.project_id}:{task.task_type}"})
            if not locked:
                continue
            policy = await self.db.scalar(select(TaskPolicy).where(
                TaskPolicy.project_id == task.project_id,
                TaskPolicy.task_type == task.task_type, TaskPolicy.is_default.is_(True),
            ).execution_options(populate_existing=True))
            if policy is not None:
                active = await self.db.scalar(select(func.count()).select_from(Task).where(
                    Task.project_id == task.project_id, Task.task_type == task.task_type,
                    Task.status.in_(("processing", "cancelling")), Task.lease_expires_at > now,
                ))
                if active >= policy.concurrency_limit:
                    continue
            run_token = uuid.uuid4()
            attempt_no = task.attempt_count + 1
            lease_seconds = max(1, task.timeout_seconds)
            # CAS：queued -> processing（携带 state_version）。
            upd = (
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.status == "queued",
                    Task.state_version == task.state_version,
                )
                .values(
                    status="processing",
                    state_version=task.state_version + 1,
                    attempt_count=attempt_no,
                    run_token=run_token,
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                    started_at=task.started_at or now,
                    error_code=None,
                    error_message=None,
                    next_run_at=now + timedelta(seconds=lease_seconds),
                )
            )
            res = await self.db.execute(upd)
            if res.rowcount != 1:
                # 竞态失败：另一 worker 已领取（不应发生，SKIP LOCKED 已保护），跳过。
                await self.db.rollback()
                raise TaskQueueError("claim 竞态失败：任务状态已变更")
            await self.db.refresh(task)
            # 创建 Attempt（唯一 run_token / (task_id, attempt_no) 约束兜底）。
            attempt = TaskAttempt(
                task_id=task.id,
                attempt_no=attempt_no,
                run_token=run_token,
                worker_id=worker_id,
                status="processing",
            )
            self.db.add(attempt)
            await self.db.flush()
            claimed.append(task)

        return claimed

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(self, *, task_id: uuid.UUID, run_token: uuid.UUID) -> bool:
        """携带 run token 的心跳；过期 worker 提交影响 0 行。"""
        now = _now()
        res = await self.db.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.run_token == run_token,
                Task.status.in_(("processing", "cancelling")),
                Task.lease_expires_at > now,
            )
            .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=300))
        )
        return res.rowcount == 1

    async def refresh_lease(
        self, *, task_id: uuid.UUID, run_token: uuid.UUID, timeout_seconds: int
    ) -> bool:
        """刷新 lease（在长时间外部调用前调用）。"""
        now = _now()
        res = await self.db.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.run_token == run_token,
                Task.status.in_(("processing", "cancelling")),
                Task.lease_expires_at > now,
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=max(1, timeout_seconds)),
            )
        )
        return res.rowcount == 1

    # ------------------------------------------------------------------
    # CAS transition
    # ------------------------------------------------------------------

    async def transition(
        self,
        *,
        task_id: uuid.UUID,
        run_token: uuid.UUID | None,
        from_status: str,
        to_status: str,
        expected_state_version: int,
        progress: int | None = None,
        error_message: str | None = None,
        error_code: str | None = None,
        result_json: dict[str, Any] | None = None,
        completed_at: datetime | None = None,
        next_run_at: datetime | None = None,
    ) -> bool:
        """CAS 状态转换；失败返回 False（竞态），不抛错。"""
        if to_status not in _TRANSITIONS.get(from_status, frozenset()):
            raise TaskQueueError(f"非法转换 {from_status} -> {to_status}")

        if from_status in ("processing", "cancelling") and run_token is None:
            raise TaskQueueError("运行中任务的状态转换必须携带 run token")
        now = _now()
        values: dict[str, Any] = {
            "status": to_status,
            "state_version": expected_state_version + 1,
            "updated_at": now,
        }
        if progress is not None:
            values["progress"] = progress
        if error_message is not None:
            values["error_message"] = error_message
        if error_code is not None:
            values["error_code"] = error_code
        if result_json is not None:
            values["result_json"] = result_json
        if to_status == "completed":
            values["progress"] = 100
            values["completed_at"] = completed_at or now
            # 完成时清空 lease/run_token，禁止迟到提交。
            values["run_token"] = None
            values["lease_owner"] = None
            values["lease_expires_at"] = None
        if to_status == "failed":
            values["completed_at"] = completed_at or now
            values["run_token"] = None
            values["lease_owner"] = None
            values["lease_expires_at"] = None
        if to_status == "cancelled":
            values["completed_at"] = completed_at or now
            values["run_token"] = None
            values["lease_owner"] = None
            values["lease_expires_at"] = None
        if to_status == "processing":
            values["started_at"] = now
            values["next_run_at"] = next_run_at or (now + timedelta(seconds=300))
        if to_status == "queued":
            # 回队：清除 lease/run_token，满足 ck_tasks_lease_required。
            values["run_token"] = None
            values["lease_owner"] = None
            values["lease_expires_at"] = None
            if next_run_at is not None:
                values["next_run_at"] = next_run_at

        stmt = update(Task).where(
            Task.id == task_id,
            Task.status == from_status,
            Task.state_version == expected_state_version,
        )
        # processing/cancelling 相关转换要求 run token 匹配。
        if run_token is not None:
            stmt = stmt.where(Task.run_token == run_token)
        if to_status == "completed":
            stmt = stmt.where(Task.lease_expires_at > now)
        res = await self.db.execute(stmt.values(**values))
        if res.rowcount == 1 and to_status in ("failed", "cancelled"):
            await self._sync_business_terminal(task_id, to_status, error_message)
        return res.rowcount == 1

    async def _sync_business_terminal(self, task_id: uuid.UUID, status: str,
                                      error_message: str | None) -> None:
        from app.workers.lifecycle import lifecycle_registry
        task = await self.db.scalar(select(Task).where(Task.id == task_id)
                                    .execution_options(populate_existing=True))
        if task is not None:
            await lifecycle_registry.terminal(self.db, task, status, error_message)

    # ------------------------------------------------------------------
    # 取消
    # ------------------------------------------------------------------

    async def request_cancel(
        self, *, task_id: uuid.UUID, cancel_requested_by: uuid.UUID
    ) -> tuple[bool, str]:
        """请求取消：queued -> cancelled（原子）；processing -> cancelling。

        返回 (成功, 最终状态)。重复请求幂等：终态返回当前状态且不改写完成时间。
        """
        now = _now()
        # First cancel descendants without holding a parent Task lock. A child can
        # publish while holding Task -> Batch; retaining a parent Task lock here and
        # then descending would create the inverse lock chain under parent cancel.
        task = await self.db.scalar(
            select(Task).where(Task.id == task_id).execution_options(populate_existing=True)
        )
        if task is None:
            return False, "not_found"
        if task.status in TERMINAL_STATUSES:
            # 终态幂等：返回当前状态，不改写完成时间。
            return True, task.status
        # Descendants first so a queued parent's immutable summary sees their actual
        # terminal states. Direct queue/API callers share this one path.
        child_ids = (await self.db.execute(select(Task.id).where(
            Task.parent_task_id == task.id, Task.status.in_(ACTIVE_STATUSES),
        ).order_by(Task.id))).scalars().all()
        for child_id in child_ids:
            await self.request_cancel(task_id=child_id, cancel_requested_by=cancel_requested_by)
        # Re-read after recursion: a worker may have reached a terminal transition.
        task = await self.db.scalar(select(Task).where(Task.id == task_id)
                                    .with_for_update().execution_options(populate_existing=True))
        if task is None:
            return False, "not_found"
        if task.status in TERMINAL_STATUSES:
            return True, task.status
        if task.status == "queued":
            res = await self.db.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.status == "queued",
                    Task.state_version == task.state_version,
                )
                .values(
                    status="cancelled",
                    state_version=task.state_version + 1,
                    completed_at=now,
                    cancel_requested_at=now,
                    cancel_requested_by=cancel_requested_by,
                    updated_at=now,
                    run_token=None,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            if res.rowcount == 1:
                await self._sync_business_terminal(task_id, "cancelled", "任务已取消")
                return True, "cancelled"
            # 竞态：可能刚被领取为 processing。零行 UPDATE 不会使事务失效；
            # 不能 rollback，否则会撤销已经完成的子任务取消。
            task = await self.db.scalar(select(Task).where(Task.id == task_id)
                                        .with_for_update().execution_options(populate_existing=True))
            if task is None:
                return False, "not_found"
            if task.status == "processing":
                return await self._mark_cancelling(task, cancel_requested_by)
            return True, task.status
        if task.status == "processing":
            return await self._mark_cancelling(task, cancel_requested_by)
        if task.status == "cancelling":
            return True, "cancelling"
        return True, task.status

    async def _mark_cancelling(
        self, task: Task, cancel_requested_by: uuid.UUID
    ) -> tuple[bool, str]:
        now = _now()
        res = await self.db.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.status == "processing",
                Task.state_version == task.state_version,
            )
            .values(
                status="cancelling",
                state_version=task.state_version + 1,
                cancel_requested_at=now,
                cancel_requested_by=cancel_requested_by,
                updated_at=now,
            )
        )
        if res.rowcount == 1:
            return True, "cancelling"
        return True, task.status

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    async def create_retry(
        self,
        *,
        source_task_id: uuid.UUID,
        idempotency_key: str,
        project_id: uuid.UUID,
        task_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        created_by: uuid.UUID,
        handler: str,
        payload: dict[str, Any],
        payload_version: int,
        max_attempts: int,
        timeout_seconds: int,
    ) -> tuple[Task | None, bool]:
        """人工 retry：创建 retry_of_task_id=source 的新 queued Task。

        同一源任务最多一个非终态人工重试后继（约束在应用层 + 唯一覆盖索引）。
        返回 ``(Task, created)``；若已有非终态后继则返回该后继与 ``False``。
        """
        # 检查源任务必须为 failed（调用方已校验，这里再确认）。
        source = (
            await self.db.execute(
                select(Task).where(Task.id == source_task_id).with_for_update()
            )
        ).scalar_one_or_none()
        if source is None or source.status != "failed":
            return None, False
        # 检查是否已有非终态人工 retry 后继。
        existing = (
            await self.db.execute(
                select(Task).where(
                    Task.retry_of_task_id == source_task_id,
                    Task.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing, False

        new_task = await self.create_task(
            project_id=project_id,
            task_type=task_type,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by=created_by,
            handler=handler,
            payload=payload,
            payload_version=payload_version,
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            policy_snapshot=source.policy_snapshot,
            resolve_policy=False,
        )
        # 绑定后继链。
        new_task.retry_of_task_id = source_task_id
        await self.db.flush()
        return new_task, True

    # ------------------------------------------------------------------
    # Reaper
    # ------------------------------------------------------------------

    async def reap_expired(
        self,
        *,
        lease_grace_seconds: int = 60,
        batch: int = 50,
        transition_log: list[tuple[uuid.UUID, str]] | None = None,
    ) -> dict[str, int]:
        """回收过期 processing 任务：有 cancel 请求 -> cancelled；
        可重试错误且有额度 -> 回 queued 退避；否则 failed。

        返回 {cancelled: n, requeued: n, failed: n, abandoned_attempts: n}。
        """
        now = _now()
        cutoff = now - timedelta(seconds=lease_grace_seconds)
        # 加锁读取过期 processing 任务。
        stmt = (
            select(Task)
            .where(
                Task.status.in_(("processing", "cancelling")),
                Task.lease_expires_at < cutoff,
            )
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        result = await self.db.execute(stmt)
        tasks = list(result.scalars().all())

        stats = {"cancelled": 0, "requeued": 0, "failed": 0, "abandoned_attempts": 0}
        for task in tasks:
            # 终止当前 attempt（abandoned）。
            await self.db.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.run_token == task.run_token,
                    TaskAttempt.status == "processing",
                )
                .values(
                    status="abandoned",
                    finished_at=now,
                    error_code=TaskErrorCode.TEMPORARY_INFRA_ERROR.value,
                    error_message="lease 过期，attempt 被 reaper 回收",
                )
            )
            stats["abandoned_attempts"] += 1

            if task.status == "cancelling" or task.cancel_requested_at is not None:
                # 有取消请求：直接 cancelled。
                changed = await self._force_terminal(task, "cancelled", None, None)
                stats["cancelled"] += 1
                if changed and transition_log is not None:
                    transition_log.append((task.id, "cancelled"))
                continue

            error_code = task.error_code or TaskErrorCode.TEMPORARY_INFRA_ERROR.value
            retriable = is_retriable(error_code) and await self.has_retry_budget(task)
            if retriable:
                delay = _backoff_delay(task.attempt_count)
                res = await self.db.execute(
                    update(Task)
                    .where(
                        Task.id == task.id,
                        Task.status == "processing",
                        Task.state_version == task.state_version,
                    )
                    .values(
                        status="queued",
                        state_version=task.state_version + 1,
                        run_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        next_run_at=now + timedelta(seconds=delay),
                        updated_at=now,
                    )
                )
                if res.rowcount == 1:
                    stats["requeued"] += 1
                    if transition_log is not None:
                        transition_log.append((task.id, "queued"))
                    continue
            # 非可重试或超限：failed。
            changed = await self._force_terminal(
                task,
                "failed",
                error_code or TaskErrorCode.TEMPORARY_INFRA_ERROR.value,
                task.error_message or "任务执行超时被回收",
            )
            stats["failed"] += 1
            if changed and transition_log is not None:
                transition_log.append((task.id, "failed"))

        return stats

    async def has_retry_budget(self, task: Task) -> bool:
        attempts = task.attempt_count
        if task.handler == "generate_batch":
            # Successful aggregation polls release the slot without spending retries.
            polls = await self.db.scalar(select(func.count()).select_from(TaskAttempt).where(
                TaskAttempt.task_id == task.id, TaskAttempt.status == "completed",
            ))
            attempts -= polls
        return attempts < task.max_attempts

    async def _force_terminal(
        self,
        task: Task,
        status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> bool:
        """reaper 专用终态写入（已持有行锁，CAS 仍校验 state_version）。"""
        now = _now()
        values: dict[str, Any] = {
            "status": status,
            "state_version": task.state_version + 1,
            "completed_at": now,
            "updated_at": now,
            "run_token": None,
            "lease_owner": None,
            "lease_expires_at": None,
        }
        if error_code is not None:
            values["error_code"] = error_code
        if error_message is not None:
            values["error_message"] = error_message
        result = await self.db.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.status.in_(("processing", "cancelling")),
                Task.state_version == task.state_version,
            )
            .values(**values)
        )
        if result.rowcount == 1:
            await self._sync_business_terminal(task.id, status, error_message)
        return result.rowcount == 1

    # ------------------------------------------------------------------
    # Attempt 审计
    # ------------------------------------------------------------------

    async def finish_attempt(
        self,
        *,
        task_id: uuid.UUID,
        run_token: uuid.UUID,
        attempt_no: int,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        retriable: bool = False,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """完成/终止一个 attempt（仅允许 processing 到终态）。"""
        now = _now()
        await self.db.execute(
            update(TaskAttempt)
            .where(
                TaskAttempt.task_id == task_id,
                TaskAttempt.run_token == run_token,
                TaskAttempt.attempt_no == attempt_no,
                TaskAttempt.status == "processing",
            )
            .values(
                status=status,
                finished_at=now,
                error_code=error_code,
                error_message=error_message,
                retriable=retriable,
                metrics_json=metrics,
            )
        )
