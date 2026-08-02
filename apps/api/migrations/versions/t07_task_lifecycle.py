"""T07: 任务生命周期、派发、重试与取消 - 持久任务队列

Revision ID: t07_task_lifecycle
Revises: 52eb455d64d3, t03_parse_job_snapshot
Create Date: 2026-08-02

背景：master 存在两个迁移 head（T02 的 52eb455d64d3 与 T03 的 t03_parse_job_snapshot
均基于 58918ea257fd），导致 `alembic upgrade head` 报 Multiple head。本迁移同时依赖
两个 head 做 merge（第一步无操作），随后落地 T07 全部 schema 变更。

合同要点（对齐 T07 §4）：
- tasks 扩展：handler/payload/payload_version/idempotency_key/state_version/
  attempt_count/max_attempts/timeout_seconds/next_run_at/lease_owner/
  lease_expires_at/heartbeat_at/run_token/cancel_requested_at/cancel_requested_by/
  error_code/result_json/retry_of_task_id/is_legacy/updated_at。
- task_status 枚举增加 cancelling；合法转换由应用层 CAS 保证（见 workers/queue.py）。
- task_attempts 审计表：唯一 (task_id, attempt_no) 与 run_token。
- 迁移前非终态且缺 payload 的旧任务标 failed + LEGACY_TASK_NOT_RESUMABLE，不猜测参数。
- claim 索引 (status, next_run_at, lease_expires_at)；列表索引 (project_id, created_at DESC)。
- downgrade 预检：若存在 cancelling 或新格式非终态任务则停止回滚；attempt 审计表
  的删除需要显式数据保留/导出决定，此处 downgrade 直接删除（测试库/开发库语义）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t07_task_lifecycle"
down_revision: str | None = ("52eb455d64d3", "t03_parse_job_snapshot")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# task_status 枚举成员（初始迁移含 queued/processing/completed/failed/cancelled）。
_TASK_STATUS_ADD = ("cancelling",)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 0. merge 两个 head：本迁移的 down_revision 已是二者，alembic 自动解决。
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 1. task_status 枚举增加 cancelling。
    #    PG 不允许在事务内使用 ALTER TYPE ADD VALUE 新增的值，因此整体重建
    #    枚举类型：status 列先转 VARCHAR，drop 旧类型，重建含 cancelling 的类型。
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute(
        "CREATE TYPE task_status AS ENUM "
        "('queued', 'processing', 'completed', 'failed', 'cancelled', 'cancelling')"
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE task_status USING status::task_status")

    # ------------------------------------------------------------------
    # 2. tasks 扩展列（均 nullable / 带默认，兼容既有行）。
    # ------------------------------------------------------------------
    op.add_column("tasks", sa.Column("handler", sa.String(length=80), nullable=True))
    op.add_column("tasks", sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("tasks", sa.Column("payload_version", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.add_column("tasks", sa.Column("state_version", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("tasks", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("tasks", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("tasks", sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")))
    op.add_column("tasks", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.add_column("tasks", sa.Column("lease_owner", sa.String(length=150), nullable=True))
    op.add_column("tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("run_token", sa.UUID(), nullable=True))
    op.add_column("tasks", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("cancel_requested_by", sa.UUID(), nullable=True))
    op.add_column("tasks", sa.Column("error_code", sa.String(length=80), nullable=True))
    op.add_column("tasks", sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("tasks", sa.Column("retry_of_task_id", sa.UUID(), nullable=True))
    op.add_column("tasks", sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("tasks", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key("fk_tasks_retry_of_task_id", "tasks", "tasks", ["retry_of_task_id"], ["id"])
    op.create_foreign_key("fk_tasks_cancel_requested_by", "tasks", "users", ["cancel_requested_by"], ["id"])

    # 幂等键唯一约束（允许 NULL，PG 对 NULL 不参与唯一）。
    op.create_unique_constraint(
        "uq_tasks_project_type_idempotency", "tasks", ["project_id", "task_type", "idempotency_key"]
    )

    # ------------------------------------------------------------------
    # 3. 存量非终态任务处置：缺少可重建 payload 的 queued/processing 旧任务
    #    标 failed + LEGACY_TASK_NOT_RESUMABLE，不猜测执行参数。
    # ------------------------------------------------------------------
    legacy_updated = op.execute(
        sa.text(
            """
            UPDATE tasks
               SET status = 'failed'
                 , completed_at = COALESCE(completed_at, now())
                 , error_code = 'LEGACY_TASK_NOT_RESUMABLE'
                 , error_message = '迁移前遗留任务，缺少可重建 payload，标记为不可恢复'
                 , is_legacy = true
             WHERE status IN ('queued', 'processing')
               AND (payload IS NULL OR handler IS NULL)
            """
        )
    )
    try:
        affected = legacy_updated.rowcount if legacy_updated is not None else 0
    except Exception:  # noqa: BLE001 - 兼容 driver 无 rowcount
        affected = 0
    if affected:
        print(f"[t07_task_lifecycle] 标记 {affected} 个迁移前遗留非终态任务为 failed")

    # 终态历史任务保留（status IN completed/failed/cancelled），并补 is_legacy=true，
    # 表示它们是旧格式行（无 handler/payload），但保持终态不变。
    op.execute(
        sa.text(
            """
            UPDATE tasks
               SET is_legacy = true
             WHERE status IN ('completed', 'failed', 'cancelled')
               AND handler IS NULL
            """
        )
    )

    # ------------------------------------------------------------------
    # 4. task_attempts 审计表。
    # ------------------------------------------------------------------
    attempt_status_enum = postgresql.ENUM(
        "processing", "completed", "failed", "timed_out", "cancelled", "abandoned",
        name="attempt_status", create_type=False,
    )
    attempt_status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("run_token", sa.UUID(), nullable=False),
        sa.Column("worker_id", sa.String(length=150), nullable=True),
        sa.Column("status", attempt_status_enum, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retriable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "attempt_no", name="uq_task_attempts_task_no"),
        sa.UniqueConstraint("run_token", name="uq_task_attempts_run_token"),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])

    # ------------------------------------------------------------------
    # 5. CHECK 约束。
    # ------------------------------------------------------------------
    op.create_check_constraint("ck_tasks_progress_range", "tasks", "progress >= 0 AND progress <= 100")
    op.create_check_constraint("ck_tasks_attempt_count_nonneg", "tasks", "attempt_count >= 0")
    op.create_check_constraint("ck_tasks_max_attempts_positive", "tasks", "max_attempts >= 1")
    op.create_check_constraint("ck_tasks_timeout_positive", "tasks", "timeout_seconds > 0")
    op.create_check_constraint(
        "ck_tasks_status_valid", "tasks",
        "status IN ('queued', 'processing', 'completed', 'failed', 'cancelled', 'cancelling')",
    )
    op.create_check_constraint(
        "ck_tasks_lease_required", "tasks",
        "(status IN ('processing', 'cancelling')) = (run_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_terminal_has_completed_at", "tasks",
        "(status IN ('completed', 'failed', 'cancelled')) = (completed_at IS NOT NULL)",
    )

    # ------------------------------------------------------------------
    # 6. 索引。
    # ------------------------------------------------------------------
    # claim 索引：(status, next_run_at, lease_expires_at)
    op.create_index("ix_tasks_claim", "tasks", ["status", "next_run_at", "lease_expires_at"])
    # 列表索引：(project_id, created_at DESC)
    op.create_index("ix_tasks_project_created", "tasks", ["project_id", sa.text("created_at DESC")])


def downgrade() -> None:
    # 回滚预检：若存在 cancelling 或新格式非终态任务，停止回滚。
    bind = op.get_bind()
    try:
        row = bind.execute(
            sa.text(
                """
                SELECT count(*) FROM tasks
                 WHERE status = 'cancelling'
                    OR (status IN ('queued', 'processing')
                        AND (payload IS NOT NULL OR handler IS NOT NULL))
                """
            )
        ).scalar()
    except sa.exc.ProgrammingError:
        # 目标列尚不存在（例如直接回到旧版本前执行）——无需预检。
        row = 0
    if row:
        raise RuntimeError(
            f"存在 {row} 个 cancelling 或新格式非终态任务；请先停止 runner 并完成/取消这些任务后再回滚。"
        )

    # 删除 attempt 审计表（显式数据保留决定：本版本为开发/测试库语义，直接删除；
    # 生产环境应在回滚前导出 task_attempts 记录）。
    op.drop_index("ix_task_attempts_task_id", table_name="task_attempts")
    op.drop_table("task_attempts")

    op.drop_index("ix_tasks_project_created", table_name="tasks")
    op.drop_index("ix_tasks_claim", table_name="tasks")

    op.drop_constraint("ck_tasks_terminal_has_completed_at", "tasks", type_="check")
    op.drop_constraint("ck_tasks_lease_required", "tasks", type_="check")
    op.drop_constraint("ck_tasks_status_valid", "tasks", type_="check")
    op.drop_constraint("ck_tasks_timeout_positive", "tasks", type_="check")
    op.drop_constraint("ck_tasks_max_attempts_positive", "tasks", type_="check")
    op.drop_constraint("ck_tasks_attempt_count_nonneg", "tasks", type_="check")
    op.drop_constraint("ck_tasks_progress_range", "tasks", type_="check")

    op.drop_constraint("uq_tasks_project_type_idempotency", "tasks", type_="unique")
    op.drop_constraint("fk_tasks_cancel_requested_by", "tasks", type_="foreignkey")
    op.drop_constraint("fk_tasks_retry_of_task_id", "tasks", type_="foreignkey")

    for column in (
        "updated_at",
        "is_legacy",
        "retry_of_task_id",
        "result_json",
        "error_code",
        "cancel_requested_by",
        "cancel_requested_at",
        "run_token",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "next_run_at",
        "timeout_seconds",
        "max_attempts",
        "attempt_count",
        "state_version",
        "idempotency_key",
        "payload_version",
        "payload",
        "handler",
    ):
        op.drop_column("tasks", column)

    # 枚举回退：把 status 列转为 text，重建不含 cancelling 的枚举并转回。
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS task_status")
    op.execute("CREATE TYPE task_status AS ENUM ('queued', 'processing', 'completed', 'failed', 'cancelled')")
    op.execute("ALTER TABLE tasks ALTER COLUMN status TYPE task_status USING status::task_status")
    op.execute("DROP TYPE IF EXISTS attempt_status")
