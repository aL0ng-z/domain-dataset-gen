"""R04: 可恢复业务任务和按类型执行策略。

Revision ID: r04_recoverable_task_policy
Revises: t11_immutable_export_snapshot
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r04_recoverable_task_policy"
down_revision = "t11_immutable_export_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE parse_job_status ADD VALUE IF NOT EXISTS 'cancelled'")
        op.execute("ALTER TYPE cleaning_job_status ADD VALUE IF NOT EXISTS 'cancelled'")
    op.add_column("tasks", sa.Column("policy_snapshot", postgresql.JSONB(), nullable=True))
    for table in ("parse_jobs", "cleaning_jobs"):
        op.add_column(table, sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_{table}_task_id", table, "tasks", ["task_id"], ["id"], ondelete="SET NULL")
        op.add_column(table, sa.Column("error_code", sa.String(80), nullable=True))
    op.add_column("cleaning_jobs", sa.Column("error_message", sa.Text(), nullable=True))
    # Legacy jobs retain NULL when no persisted Task exists; active jobs can be recovered.
    for table, handler, key in (("parse_jobs", "parse_document", "parse_job_id"),
                                 ("cleaning_jobs", "clean_document", "cleaning_job_id")):
        op.execute(sa.text(f"""
            UPDATE {table} j SET task_id = t.id FROM (
                SELECT DISTINCT ON (payload->>'{key}') id, payload->>'{key}' AS job_id
                FROM tasks WHERE handler = '{handler}'
                ORDER BY payload->>'{key}', created_at DESC, id DESC
            ) t WHERE j.id::text = t.job_id
        """))
    for name, condition in (("retries_nonnegative", "max_retries >= 0"),
                             ("timeout_positive", "timeout_seconds > 0"),
                             ("concurrency_positive", "concurrency_limit > 0"),
                             ("task_type_valid", "task_type IN ('parse','clean','chunk','generate','generate_batch','export')")):
        op.create_check_constraint(f"ck_task_policies_{name}", "task_policies", condition)
    # Earlier schemas allowed duplicates; retain the most recently chosen/default row.
    op.execute("""UPDATE task_policies SET is_default = false WHERE id IN (
        SELECT id FROM (SELECT id, row_number() OVER (
            PARTITION BY project_id, task_type ORDER BY updated_at DESC, id DESC
        ) AS n FROM task_policies WHERE is_default = true) ranked WHERE n > 1
    )""")
    op.create_index("uq_task_policies_default_type", "task_policies", ["project_id", "task_type"],
                    unique=True, postgresql_where=sa.text("is_default = true"))


def downgrade() -> None:
    op.drop_index("uq_task_policies_default_type", table_name="task_policies")
    for name in ("retries_nonnegative", "timeout_positive", "concurrency_positive", "task_type_valid"):
        op.drop_constraint(f"ck_task_policies_{name}", "task_policies", type_="check")
    op.drop_column("cleaning_jobs", "error_message")
    for table, enum in (("parse_jobs", "parse_job_status"), ("cleaning_jobs", "cleaning_job_status")):
        op.drop_column(table, "error_code")
        op.drop_constraint(f"fk_{table}_task_id", table, type_="foreignkey")
        op.drop_column(table, "task_id")
        op.execute(sa.text(f"UPDATE {table} SET status = 'failed' WHERE status = 'cancelled'"))
        op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN status DROP DEFAULT"))
        op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN status TYPE varchar(20) USING status::text"))
        op.execute(sa.text(f"DROP TYPE {enum}"))
        op.execute(sa.text(f"CREATE TYPE {enum} AS ENUM ('queued','processing','completed','failed')"))
        op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN status TYPE {enum} USING status::{enum}"))
    op.drop_column("tasks", "policy_snapshot")
