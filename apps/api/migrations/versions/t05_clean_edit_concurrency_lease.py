"""T05: 清洗编辑并发与租约、CleanedDocumentVersion 原子发布

Revision ID: t05_clean_edit_concurrency_lease
Revises: 52eb455d64d3
Create Date: 2026-08-02

合同要点（对齐 T05 §4.1/§4.3/§4.4）：
- sections 新增 content_revision BIGINT NOT NULL DEFAULT 0 + CHECK(content_revision >= 0)。
- section_leases 新增部分唯一索引 UNIQUE(section_id) WHERE released_at IS NULL、
  查询索引 (section_id, expires_at) 与 CHECK(expires_at > acquired_at)。
- section_revisions 新增 from_revision/to_revision + CHECK(to_revision = from_revision + 1)。
- cleaned_document_versions 新增 source_revision_map JSONB / source_revision_sha256 CHAR(64) /
  content_sha256 CHAR(64) / merge_idempotency_key VARCHAR(128)，以及
  UNIQUE(document_id, merge_idempotency_key) 与 UNIQUE(artifact_key)。
- 建唯一索引前：过期未释放租约置 released_at=expires_at；同一 Section 多条有效记录
  确定性保留一条（expires_at 最新，再按 acquired_at/id），其余标记释放并输出审计计数。
- 重复 lease 预检 + 清理 + 建索引在同一事务完成，避免窗口期新增重复租约。
- 历史 CleanedDocumentVersion 行标记 legacy（新列允许 NULL），不伪造 revision/hash。
- downgrade 仅移除新增索引、约束与列，不删除既有租约/修订内容。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t05_clean_edit_concurrency_lease"
down_revision: str | Sequence[str] | None = "merge_t02_t03_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _clean_duplicate_leases(conn) -> tuple[int, int]:
    """预检并清理迁移前重复/过期 lease，返回 (过期清理数, 重复清理数)。

    规则（任务卡 §4.3）：
    1. 已过期且未释放 -> released_at = expires_at；
    2. expires_at <= acquired_at 的异常行（无法满足 CHECK）标记为已释放；
    3. 同一 Section 仍有多条有效记录时，仅保留 expires_at 最新（再按 acquired_at/id
       排序）的第一条，其余标记释放并输出审计计数。
    """
    expired = conn.execute(
        sa.text(
            "UPDATE section_leases SET released_at = expires_at "
            "WHERE released_at IS NULL AND expires_at <= now()"
        )
    ).rowcount

    # expires_at <= acquired_at 的异常行无法满足新增 CHECK，标记为已释放。
    invalid = conn.execute(
        sa.text(
            "UPDATE section_leases SET released_at = acquired_at "
            "WHERE released_at IS NULL AND expires_at <= acquired_at"
        )
    ).rowcount

    # 确定性保留：窗口函数按 (section_id) 分组，保留每 Section 最新一条有效租约。
    dups = conn.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY section_id
                           ORDER BY expires_at DESC, acquired_at DESC, id DESC
                       ) AS rn
                  FROM section_leases
                 WHERE released_at IS NULL
            )
            UPDATE section_leases sl
               SET released_at = now()
              FROM ranked r
             WHERE sl.id = r.id AND r.rn > 1
            """
        )
    ).rowcount
    return int(expired + invalid), int(dups)


def upgrade() -> None:
    conn = op.get_bind()

    # --- sections.content_revision ---
    op.add_column(
        "sections",
        sa.Column("content_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_sections_content_revision_nonneg",
        "sections",
        "content_revision >= 0",
    )

    # --- section_leases：预检/清理重复租约（与建索引同一事务）---
    expired_n, dup_n = _clean_duplicate_leases(conn)
    # 审计计数直接输出到迁移日志。
    print(f"[T05] 租约清理审计：过期 {expired_n} 条，重复 {dup_n} 条", flush=True)

    op.create_index(
        "uq_section_leases_active_section",
        "section_leases",
        ["section_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ix_section_leases_section_expires",
        "section_leases",
        ["section_id", "expires_at"],
    )
    op.create_check_constraint(
        "ck_section_leases_expiry_after_acquire",
        "section_leases",
        "expires_at > acquired_at",
    )

    # --- section_revisions：from/to revision ---
    op.add_column(
        "section_revisions",
        sa.Column("from_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "section_revisions",
        sa.Column("to_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_section_revisions_contiguous",
        "section_revisions",
        "to_revision = from_revision + 1",
    )

    # --- cleaned_document_versions：原子发布字段 ---
    op.add_column(
        "cleaned_document_versions",
        sa.Column("source_revision_map", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "cleaned_document_versions",
        sa.Column("source_revision_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "cleaned_document_versions",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "cleaned_document_versions",
        sa.Column("merge_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_cleaned_doc_ver_doc_idem",
        "cleaned_document_versions",
        ["document_id", "merge_idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_cleaned_doc_ver_artifact_key",
        "cleaned_document_versions",
        ["artifact_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_cleaned_doc_ver_artifact_key", "cleaned_document_versions", type_="unique")
    op.drop_constraint("uq_cleaned_doc_ver_doc_idem", "cleaned_document_versions", type_="unique")
    op.drop_column("cleaned_document_versions", "merge_idempotency_key")
    op.drop_column("cleaned_document_versions", "content_sha256")
    op.drop_column("cleaned_document_versions", "source_revision_sha256")
    op.drop_column("cleaned_document_versions", "source_revision_map")

    op.drop_constraint("ck_section_revisions_contiguous", "section_revisions", type_="check")
    op.drop_column("section_revisions", "to_revision")
    op.drop_column("section_revisions", "from_revision")

    op.drop_constraint("ck_section_leases_expiry_after_acquire", "section_leases", type_="check")
    op.drop_index("ix_section_leases_section_expires", table_name="section_leases")
    op.drop_index("uq_section_leases_active_section", table_name="section_leases")

    op.drop_constraint("ck_sections_content_revision_nonneg", "sections", type_="check")
    op.drop_column("sections", "content_revision")
