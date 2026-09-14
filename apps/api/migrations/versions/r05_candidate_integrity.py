"""R05: Candidate 内容版本、配置默认项与来源区间。

Revision ID: r05_candidate_integrity
Revises: r04_recoverable_task_policy
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r05_candidate_integrity"
down_revision = "r04_recoverable_task_policy"
branch_labels = None
depends_on = None


_DEFAULT_INDEXES = (
    ("model_configs", "uq_model_configs_default"),
    ("parser_profiles", "uq_parser_profiles_default"),
    ("chunk_profiles", "uq_chunk_profiles_default"),
    ("export_profiles", "uq_export_profiles_default"),
)


def _clear_ambiguous_defaults(table_name: str) -> None:
    """旧数据若有多个默认项，全部清空而不臆测哪个应被保留。"""
    op.execute(
        sa.text(
            f"""
            UPDATE {table_name}
               SET is_default = false
             WHERE is_default = true
               AND project_id IN (
                   SELECT project_id
                     FROM {table_name}
                    WHERE is_default = true
                    GROUP BY project_id
                   HAVING count(*) > 1
               )
            """
        )
    )


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("content_revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "candidates",
        sa.Column("reviewed_content_revision", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_candidates_content_revision_positive",
        "candidates",
        "content_revision >= 1",
    )
    op.create_check_constraint(
        "ck_candidates_reviewed_content_revision_positive",
        "candidates",
        "reviewed_content_revision IS NULL OR reviewed_content_revision >= 1",
    )

    # 来源页码区间由 provenance/export 子任务写入；本迁移仅提供存储列。
    op.add_column(
        "cleaned_document_versions",
        sa.Column(
            "source_intervals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    for table_name, index_name in _DEFAULT_INDEXES:
        _clear_ambiguous_defaults(table_name)
        op.create_index(
            index_name,
            table_name,
            ["project_id"],
            unique=True,
            postgresql_where=sa.text("is_default = true"),
        )


def downgrade() -> None:
    for table_name, index_name in reversed(_DEFAULT_INDEXES):
        op.drop_index(index_name, table_name=table_name)

    op.drop_column("cleaned_document_versions", "source_intervals")

    op.drop_constraint(
        "ck_candidates_reviewed_content_revision_positive",
        "candidates",
        type_="check",
    )
    op.drop_constraint(
        "ck_candidates_content_revision_positive",
        "candidates",
        type_="check",
    )
    op.drop_column("candidates", "reviewed_content_revision")
    op.drop_column("candidates", "content_revision")
