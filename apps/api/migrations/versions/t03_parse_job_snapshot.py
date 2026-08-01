"""T03: ParseJob 冻结快照字段与不可变约束

Revision ID: t03_parse_job_snapshot
Revises: 58918ea257fd
Create Date: 2026-08-01

合同要点（对齐 T03 §4）：
- ParseJob 新增 parser_profile_snapshot/parser_profile_sha256/endpoint_policy_snapshot/
  endpoint_policy_ref/endpoint_policy_version/endpoint_policy_sha256/snapshot_schema_version/frozen_at。
- snapshot_schema_version=0 允许空 hash（legacy_unavailable）；>=1 时两个 snapshot、
  两个 hash、endpoint_policy_ref/version 必须全部非空（数据库 CHECK）。
- 数据库触发器禁止 UPDATE 改写任一快照、ref、version 或 hash 字段；状态更新不受影响。
- 迁移前已存在的 ParseJob 标记 snapshot_schema_version=0 + legacy_unavailable，
  不伪造历史配置。
- downgrade 恢复原列；无需回滚历史数据。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t03_parse_job_snapshot"
down_revision: str | None = "58918ea257fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 不可变列集合（数据库触发器禁止 UPDATE）。
_IMMUTABLE_COLUMNS = (
    "parser_profile_snapshot",
    "parser_profile_sha256",
    "endpoint_policy_snapshot",
    "endpoint_policy_ref",
    "endpoint_policy_version",
    "endpoint_policy_sha256",
    "snapshot_schema_version",
    "frozen_at",
)

_TRIGGER_NAME = "parse_jobs_snapshot_immutable"
_FUNCTION_NAME = "guard_parse_jobs_snapshot_immutable"


def _guard_function_sql() -> str:
    comparisons = " OR ".join(f"NEW.{col} IS DISTINCT FROM OLD.{col}" for col in _IMMUTABLE_COLUMNS)
    return f"""
CREATE OR REPLACE FUNCTION {_FUNCTION_NAME}() RETURNS trigger AS $$
BEGIN
    IF {comparisons} THEN
        RAISE EXCEPTION 'parse_jobs snapshot columns are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column("parse_jobs", sa.Column("snapshot_schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("parse_jobs", sa.Column("parser_profile_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("parse_jobs", sa.Column("parser_profile_sha256", sa.String(length=64), nullable=True))
    op.add_column("parse_jobs", sa.Column("endpoint_policy_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("parse_jobs", sa.Column("endpoint_policy_ref", sa.String(length=200), nullable=True))
    op.add_column("parse_jobs", sa.Column("endpoint_policy_version", sa.String(length=100), nullable=True))
    op.add_column("parse_jobs", sa.Column("endpoint_policy_sha256", sa.String(length=64), nullable=True))
    op.add_column("parse_jobs", sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True))

    # 存量历史 ParseJob：标记 legacy_unavailable（schema_version=0，hash 允许为空）。
    op.execute(
        """
        UPDATE parse_jobs
           SET snapshot_schema_version = 0
             , parser_profile_snapshot = jsonb_build_object('legacy_unavailable', true)
             , endpoint_policy_snapshot = jsonb_build_object('legacy_unavailable', true)
         WHERE snapshot_schema_version IS NULL OR snapshot_schema_version = 1
        """
    )

    # 完整性约束：schema_version>=1 时快照/hash/ref/version 必须全部非空。
    op.create_check_constraint(
        "ck_parse_jobs_snapshot_complete",
        "parse_jobs",
        """
        (snapshot_schema_version = 0)
        OR
        (
            snapshot_schema_version >= 1
            AND parser_profile_snapshot IS NOT NULL
            AND parser_profile_sha256 IS NOT NULL
            AND length(parser_profile_sha256) = 64
            AND endpoint_policy_snapshot IS NOT NULL
            AND endpoint_policy_ref IS NOT NULL
            AND endpoint_policy_version IS NOT NULL
            AND endpoint_policy_sha256 IS NOT NULL
            AND length(endpoint_policy_sha256) = 64
        )
        """,
    )

    # 触发器：禁止 UPDATE 改写任一快照/ref/version/hash。
    op.execute(_guard_function_sql())
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON parse_jobs;")
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_NAME}
            BEFORE UPDATE OF {", ".join(_IMMUTABLE_COLUMNS)} ON parse_jobs
            FOR EACH ROW EXECUTE FUNCTION {_FUNCTION_NAME}();
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON parse_jobs;")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION_NAME}();")
    op.drop_constraint("ck_parse_jobs_snapshot_complete", "parse_jobs", type_="check")
    for column in (
        "frozen_at",
        "endpoint_policy_sha256",
        "endpoint_policy_version",
        "endpoint_policy_ref",
        "endpoint_policy_snapshot",
        "parser_profile_sha256",
        "parser_profile_snapshot",
        "snapshot_schema_version",
    ):
        op.drop_column("parse_jobs", column)
