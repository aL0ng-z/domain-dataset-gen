import uuid
from datetime import datetime

from domain.schemas import BaseSchema


class TaskResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    task_type: str
    entity_type: str
    entity_id: uuid.UUID
    parent_task_id: uuid.UUID | None
    status: str
    progress: int
    error_message: str | None
    created_by: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    # ---- T07：可恢复执行记录字段 ----
    state_version: int
    attempt_count: int
    max_attempts: int
    next_run_at: datetime
    cancel_requested_at: datetime | None
    retry_of_task_id: uuid.UUID | None
    can_cancel: bool
    can_retry: bool
    error_code: str | None
    # 错误展示可更新；Attempt 历史不可覆盖。result_json 不含敏感内部字段。
    result_json: dict | None = None

    @classmethod
    def from_task(cls, task) -> "TaskResponse":
        """从 ORM Task 构建响应，计算 can_cancel / can_retry。

        can_cancel：queued/processing/cancelling 可取消（cancelling 是取消进行中，
        重复请求幂等返回当前状态）。
        can_retry：仅 failed 可人工 retry（completed/cancelled 返回 409）。
        """
        status = task.status
        return cls(
            id=task.id,
            project_id=task.project_id,
            task_type=task.task_type,
            entity_type=task.entity_type,
            entity_id=task.entity_id,
            parent_task_id=task.parent_task_id,
            status=status,
            progress=task.progress,
            error_message=task.error_message,
            created_by=task.created_by,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            state_version=task.state_version,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            next_run_at=task.next_run_at,
            cancel_requested_at=task.cancel_requested_at,
            retry_of_task_id=task.retry_of_task_id,
            can_cancel=status in ("queued", "processing", "cancelling"),
            can_retry=status == "failed",
            error_code=task.error_code,
            result_json=task.result_json,
        )


class TaskAttemptResponse(BaseSchema):
    id: uuid.UUID
    task_id: uuid.UUID
    attempt_no: int
    worker_id: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    retriable: bool
    # 不返回 metrics_json 中的敏感内部字段；仅暴露安全摘要。
    metrics: dict | None = None

    @classmethod
    def from_attempt(cls, attempt) -> "TaskAttemptResponse":
        metrics = attempt.metrics_json or {}
        # 仅暴露安全摘要：latency 等可展示字段，丢弃内部/敏感字段。
        safe_metrics = {
            k: v for k, v in metrics.items() if k in ("latency_ms", "input_tokens", "output_tokens")
        }
        return cls(
            id=attempt.id,
            task_id=attempt.task_id,
            attempt_no=attempt.attempt_no,
            worker_id=attempt.worker_id,
            status=attempt.status,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            error_code=attempt.error_code,
            error_message=attempt.error_message,
            retriable=attempt.retriable,
            metrics=safe_metrics or None,
        )


class TaskCancelResponse(BaseSchema):
    """取消任务响应：返回当前任务状态。"""

    id: uuid.UUID
    status: str
    state_version: int
    completed_at: datetime | None
