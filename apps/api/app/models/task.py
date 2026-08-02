"""任务持久化队列模型（T07）。

Task 成为可恢复的真实执行记录，而非 HTTP 请求内 BackgroundTasks 的进度标签。
所有状态/进度变更递增 state_version，供 compare-and-set（CAS）与事件去重。

状态图（合法转换，见任务卡 §4.1）：
    queued    -> processing | cancelled
    processing -> queued | completed | failed | cancelling
    cancelling -> cancelled

终态 completed/failed/cancelled 不再原地返回 queued；人工 retry 创建新 Task。
所有转换必须使用 WHERE id=? AND status=? AND state_version=? 或等价锁定，
受影响行数不是 1 即视为竞态失败。

敏感/大对象（API key、临时下载 URL、PDF 二进制、Prompt 全文）绝不写入 payload；
payload 只存资源 id 与非敏感选项，且带 payload_version 版本号。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

task_status_enum = ENUM(
    "queued", "processing", "completed", "failed", "cancelled", "cancelling",
    name="task_status", create_type=True,
)

# Attempt 状态：processing/completed/failed/timed_out/cancelled/abandoned。
attempt_status_enum = ENUM(
    "processing", "completed", "failed", "timed_out", "cancelled", "abandoned",
    name="attempt_status", create_type=True,
)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)

    # ---- T07：handler / payload（无密钥、版本化）----
    handler: Mapped[str] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payload_version: Mapped[int] = mapped_column(Integer, nullable=True)
    # 幂等键：唯一约束 (project_id, task_type, idempotency_key)。
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ---- 状态机与 CAS ----
    status: Mapped[str] = mapped_column(task_status_enum, nullable=False, default="queued")
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ---- 执行策略（创建时冻结快照）----
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("300"))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ---- 执行 lease / 取消 ----
    lease_owner: Mapped[str | None] = mapped_column(String(150), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # 人工 retry 后继链：同一源任务最多一个非终态人工重试后继。
    retry_of_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True
    )

    # 仅迁移前无法恢复 payload 的历史行可为 true。
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- 约束 ----
    __table_args__ = (
        UniqueConstraint(
            "project_id", "task_type", "idempotency_key",
            name="uq_tasks_project_type_idempotency",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_tasks_progress_range"),
        CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_nonneg"),
        CheckConstraint("max_attempts >= 1", name="ck_tasks_max_attempts_positive"),
        CheckConstraint("timeout_seconds > 0", name="ck_tasks_timeout_positive"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'cancelled', 'cancelling')",
            name="ck_tasks_status_valid",
        ),
        # processing/cancelling 必须有 run token 与 lease；终态必须有 completed_at。
        CheckConstraint(
            "(status IN ('processing', 'cancelling')) = (run_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_tasks_lease_required",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed', 'cancelled')) = (completed_at IS NOT NULL)",
            name="ck_tasks_terminal_has_completed_at",
        ),
    )


class TaskAttempt(Base):
    """Attempt 审计表：不可覆盖或删除的历史记录。

    - 唯一约束 (task_id, attempt_no) 与 run_token。
    - Task 错误展示可更新，Attempt 历史不可覆盖或删除。
    - max_attempts = TaskPolicy.max_retries + 1，Task 创建时冻结策略。
    """

    __tablename__ = "task_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    run_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str] = mapped_column(attempt_status_enum, nullable=False, default="processing")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retriable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metrics_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_task_attempts_task_no"),
        UniqueConstraint("run_token", name="uq_task_attempts_run_token"),
        CheckConstraint("attempt_no >= 1", name="ck_task_attempts_no_positive"),
    )


class LlmUsageLog(Base):
    __tablename__ = "llm_usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)
    model_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("model_configs.id"))
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_templates.id"), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
