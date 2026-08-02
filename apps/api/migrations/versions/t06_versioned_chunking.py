"""T06: 版本化切分与 Token 预算 - ChunkSet 版本化、legacy 回填、Chunk 强制绑定

Revision ID: t06_versioned_chunking
Revises: t07_task_lifecycle, t05_clean_edit_concurrency_lease
Create Date: 2026-08-03

背景：master 存在两个 head（t05_clean_edit_concurrency_lease 与
t07_task_lifecycle），本迁移同时依赖二者做 merge，随后落地 T06 全部 schema 变更。

合同要点（对齐 T06 §4）：
- chunk_sets 新增：version（Document 内唯一）、is_legacy、idempotency_key、
  source_sha256、output_sha256、splitter_version、error_message、completed_at、
  task_id、cleaned_document_version_id 收紧、config 冻结校验。
- chunk_set_status 枚举增加 failed/cancelled；重建枚举（PG 不允许事务内
  ALTER TYPE ADD VALUE 后立刻使用新值）。
- chunks.chunk_set_id 迁移后 NOT NULL；唯一 (chunk_set_id, ordinal)；
  新增索引 (chunk_set_id, ordinal) 与 (document_id, chunk_set_id)。
- chunk_profiles 增加 CHECK（max_tokens>0、overlap>=0、overlap<max_tokens）。
- 部分唯一索引：同一 Document 最多一个 pending/processing 集合。
- 回填：按 created_at,id 为现有 ChunkSet 分配稳定 version；无法绑定 T07 持久
  Task 的迁移前记录置 is_legacy=true；对 chunk_set_id IS NULL 的 Chunk 按
  Document 创建 is_legacy=true 隔离集合并稳定重排 ordinal。
- 非法 active_chunk_set_id（指向异文档或为空）清空，不猜测填充。
- downgrade 预检：若存在 failed/cancelled 集合则停止；回滚只移除新增约束/列，
  不删除历史 Chunk，保留旧 nullable 结构。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t06_versioned_chunking"
down_revision: str | Sequence[str] | None = ("t07_task_lifecycle", "t05_clean_edit_concurrency_lease")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild_chunk_set_status_enum() -> None:
    """把 chunk_set_status 枚举整体重建为 7 值（含 failed/cancelled）。

    PG 不允许在事务内 ``ALTER TYPE ADD VALUE`` 后立刻使用新值，因此按 T07 同款
    方案：先移除列默认值（避免 DROP TYPE 被默认值依赖阻止），列转 VARCHAR，
    drop 旧类型，重建含新值的类型，再转回并恢复默认值。
    """
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS chunk_set_status")
    op.execute(
        "CREATE TYPE chunk_set_status AS ENUM "
        "('pending', 'processing', 'review_pending', 'completed', 'rejected', 'failed', 'cancelled')"
    )
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status TYPE chunk_set_status USING status::chunk_set_status")
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status SET DEFAULT 'pending'::chunk_set_status")


def _backfill_versions_and_legacy(conn) -> tuple[int, int]:
    """为现有 ChunkSet 分配稳定 version 并标记 legacy。

    规则（任务卡 §4.3.1）：
    - 按 ``created_at, id`` 在各 Document 内分配稳定 version（1, 2, ...）。
    - 先尝试把可追溯的 chunk 类型持久 Task 绑定到 task_id（payload 携带
      chunk_set_id 且 entity_id=document_id）；仍无 task 的行一律置
      is_legacy=true，不得用猜测的 task_id 通过新 CHECK。
    - 迁移生成的 legacy set 的 summary_json 写入 provenance=legacy_unverified。

    返回 (version 回填行数, legacy 标记行数)。
    """
    # 1. 分配 version：按 created_at,id 排序逐文档编号。
    versioned = conn.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY document_id
                           ORDER BY created_at, id
                       ) AS ver
                  FROM chunk_sets
            )
            UPDATE chunk_sets cs
               SET version = r.ver
              FROM ranked r
             WHERE cs.id = r.id
            """
        )
    ).rowcount

    # 2. 先绑定可追溯 task_id（payload 携带 chunk_set_id 且属于同 document）。
    conn.execute(
        sa.text(
            """
            UPDATE chunk_sets cs
               SET task_id = t.id
              FROM tasks t
             WHERE cs.task_id IS NULL
               AND t.task_type = 'chunk'
               AND t.entity_id = cs.document_id
               AND t.payload ? 'chunk_set_id'
               AND (t.payload->>'chunk_set_id')::uuid = cs.id
            """
        )
    )

    # 3. 无 task_id 或缺失非 legacy 必填字段（cleaned version/profile/config）的
    #    迁移前记录一律置 legacy（不伪造 task_id，也不为旧数据猜测溯源）。
    legacy = conn.execute(
        sa.text(
            """
            UPDATE chunk_sets cs
               SET is_legacy = true
                 , summary_json = COALESCE(summary_json, '{}'::jsonb)
                     || '{"provenance": "legacy_unverified"}'::jsonb
             WHERE cs.task_id IS NULL
                OR cs.cleaned_document_version_id IS NULL
                OR cs.chunk_profile_id IS NULL
                OR cs.strategy IS NULL
                OR cs.config_json IS NULL
            """
        )
    ).rowcount

    return int(versioned), int(legacy)


def _backfill_orphan_chunks(conn) -> tuple[int, int]:
    """为 chunk_set_id IS NULL 的 Chunk 创建隔离 legacy set 并绑定。

    规则（任务卡 §4.3.2）：
    - 按 Document 分组，每组创建一个 is_legacy=true 的隔离集合（id 用确定性
      UUID v5 命名空间，保证同 Document 迁移幂等；文档 UUID 空间内稳定）。
    - 该集合不自动声称具有 cleaned version/profile/hash：cleaned_document_version_id
      / chunk_profile_id / strategy / config_json 保持 NULL（CHECK 只约束
      is_legacy=false 的行）。
    - 按 created_at,id 稳定重排 ordinal 后绑定，Chunk id 不变，下游 FK 不受影响。
    - summary_json 写入 provenance=legacy_unverified 与迁移计数。

    返回 (创建集合数, 绑定 Chunk 数)。
    """
    # 3.1 为每个含孤儿 Chunk 的 Document 创建隔离 legacy set。
    #  created_by 取该 Document 上传者（documents.uploaded_by），保证非空。
    created = conn.execute(
        sa.text(
            """
            INSERT INTO chunk_sets (
                id, document_id, cleaned_document_version_id, chunk_profile_id,
                strategy, config_json, status, total_chunks, total_tokens,
                summary_json, created_by, created_at, updated_at, version, is_legacy
            )
            SELECT
                md5(('legacy:' || c.document_id::text)::bytea)::uuid,
                c.document_id,
                NULL, NULL, NULL, NULL, 'completed',
                0, 0,
                jsonb_build_object(
                    'provenance', 'legacy_unverified',
                    'migration_backfill', 'orphan_chunks',
                    'backfilled_at', now()
                ),
                d.uploaded_by,
                now(), now(), 1, true
            FROM (SELECT DISTINCT document_id FROM chunks WHERE chunk_set_id IS NULL) c
            JOIN documents d ON d.id = c.document_id
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    created_count = created.rowcount

    # 3.2 按 created_at,id 稳定重排 ordinal 后绑定（同一事务内）。
    bound = conn.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY document_id ORDER BY created_at, id
                       ) - 1 AS new_ordinal
                  FROM chunks
                 WHERE chunk_set_id IS NULL
            )
            UPDATE chunks c
               SET chunk_set_id = md5(('legacy:' || c.document_id::text)::bytea)::uuid,
                   ordinal = r.new_ordinal
              FROM ranked r
             WHERE c.id = r.id
            """
        )
    ).rowcount

    return int(created_count), int(bound)


def _validate_chunk_set_consistency(conn) -> None:
    """回填后校验：任何 active_chunk_set_id 指向异文档/空 -> 清空（不猜测填充）。"""
    fixed = conn.execute(
        sa.text(
            """
            UPDATE documents d
               SET active_chunk_set_id = NULL
             WHERE d.active_chunk_set_id IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1 FROM chunk_sets cs
                     WHERE cs.id = d.active_chunk_set_id
                       AND cs.document_id = d.id
               )
            """
        )
    ).rowcount
    print(f"[T06] 清空非法 active_chunk_set_id：{fixed} 个", flush=True)


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. chunk_set_status 枚举增加 failed/cancelled。
    # ------------------------------------------------------------------
    _rebuild_chunk_set_status_enum()

    # ------------------------------------------------------------------
    # 2. chunk_sets 新增列（先 nullable，回填后再收紧）。
    # ------------------------------------------------------------------
    op.add_column("chunk_sets", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column("chunk_sets", sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("chunk_sets", sa.Column("idempotency_key", sa.String(length=100), nullable=True))
    op.add_column("chunk_sets", sa.Column("source_sha256", sa.String(length=64), nullable=True))
    op.add_column("chunk_sets", sa.Column("output_sha256", sa.String(length=64), nullable=True))
    op.add_column("chunk_sets", sa.Column("splitter_version", sa.String(length=100), nullable=True))
    op.add_column("chunk_sets", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("chunk_sets", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chunk_sets", sa.Column("task_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_chunk_sets_task_id", "chunk_sets", "tasks", ["task_id"], ["id"])

    # ------------------------------------------------------------------
    # 3. 回填：分配稳定 version、标记 legacy、绑定可追溯 task_id。
    # ------------------------------------------------------------------
    versioned, legacy_bound = _backfill_versions_and_legacy(conn)
    print(f"[T06] 版本回填：{versioned} 个 set 分配 version；legacy/可追溯标记：{legacy_bound}", flush=True)

    # ------------------------------------------------------------------
    # 4. 孤儿 Chunk 回填：按 Document 创建隔离 legacy set 并绑定。
    # ------------------------------------------------------------------
    created_sets, bound_chunks = _backfill_orphan_chunks(conn)
    print(f"[T06] 孤儿 Chunk 回填：创建 {created_sets} 个隔离 legacy set，绑定 {bound_chunks} 个 Chunk", flush=True)

    # ------------------------------------------------------------------
    # 5. 非法 active pointer 清空（不猜测）。
    # ------------------------------------------------------------------
    _validate_chunk_set_consistency(conn)

    # ------------------------------------------------------------------
    # 5.1 同一 Document 存在多个 pending/processing 集合时，保留最新一个，
    #     其余标记 failed（诚实保留可追溯性，不伪造完成/拒绝状态）。
    #     （部分唯一索引 uq_chunk_sets_single_active 要求每文档至多一个活跃集合。）
    # ------------------------------------------------------------------
    precleaned = conn.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY document_id
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                  FROM chunk_sets
                 WHERE status IN ('pending', 'processing')
            )
            UPDATE chunk_sets cs
               SET status = 'failed'
                 , error_message = '迁移前存在多个活跃集合，保留最新者并标记本集合失败'
              FROM ranked r
             WHERE cs.id = r.id AND r.rn > 1
            """
        )
    ).rowcount
    if precleaned:
        print(f"[T06] 迁移前活跃集合消歧：{precleaned} 个标记 failed", flush=True)

    # ------------------------------------------------------------------
    # 6. 回填完成且验证零 NULL 后，收紧约束。
    # ------------------------------------------------------------------
    chunk_nulls = conn.execute(
        sa.text("SELECT count(*) FROM chunks WHERE chunk_set_id IS NULL")
    ).scalar()
    if chunk_nulls:
        raise RuntimeError(
            f"[T06] 回填后仍有 {chunk_nulls} 个 Chunk 的 chunk_set_id 为 NULL；"
            "命中任务卡 §12 停止条件，不得在 NULL 下收紧约束。"
        )

    # version 必须非空（回填已完成）。
    version_nulls = conn.execute(
        sa.text("SELECT count(*) FROM chunk_sets WHERE version IS NULL")
    ).scalar()
    if version_nulls:
        raise RuntimeError(
            f"[T06] 回填后仍有 {version_nulls} 个 ChunkSet 的 version 为 NULL；停止。"
        )

    # ------------------------------------------------------------------
    # 7. 约束收紧。
    # ------------------------------------------------------------------
    # chunks.chunk_set_id -> NOT NULL（FK 默认 RESTRICT/限制删除）。
    op.alter_column("chunks", "chunk_set_id", nullable=False)
    op.create_unique_constraint("uq_chunks_set_ordinal", "chunks", ["chunk_set_id", "ordinal"])
    op.create_check_constraint("ck_chunks_ordinal_nonneg", "chunks", "ordinal >= 0")
    op.create_check_constraint("ck_chunks_token_count_positive", "chunks", "token_count > 0")
    op.create_index("ix_chunks_set_ordinal", "chunks", ["chunk_set_id", "ordinal"])
    op.create_index("ix_chunks_doc_set", "chunks", ["document_id", "chunk_set_id"])

    # chunk_sets 唯一约束与部分唯一索引。
    op.alter_column("chunk_sets", "version", nullable=False)
    op.create_unique_constraint("uq_chunk_sets_doc_version", "chunk_sets", ["document_id", "version"])
    op.create_unique_constraint(
        "uq_chunk_sets_doc_idem", "chunk_sets", ["document_id", "idempotency_key"]
    )
    op.create_unique_constraint("uq_chunk_sets_task_id", "chunk_sets", ["task_id"])
    op.create_index(
        "uq_chunk_sets_single_active",
        "chunk_sets",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.create_check_constraint(
        "ck_chunk_sets_legacy_required_fields",
        "chunk_sets",
        """
        is_legacy = true OR (
            cleaned_document_version_id IS NOT NULL
            AND chunk_profile_id IS NOT NULL
            AND strategy IS NOT NULL
            AND config_json IS NOT NULL
            AND task_id IS NOT NULL
        )
        """,
    )
    op.create_check_constraint(
        "ck_chunk_sets_version_positive", "chunk_sets", "version > 0"
    )

    # chunk_profiles CHECK。
    op.create_check_constraint("ck_chunk_profiles_max_tokens_positive", "chunk_profiles", "max_tokens > 0")
    op.create_check_constraint(
        "ck_chunk_profiles_overlap_range", "chunk_profiles",
        "overlap_tokens >= 0 AND overlap_tokens < max_tokens",
    )

    # document active_chunk_set_id FK 语义（已存在，无需重建）。

    # ------------------------------------------------------------------
    # 8. 迁移后索引/说明输出。
    # ------------------------------------------------------------------
    print("[T06] 迁移完成：version/legacy/hash/config 冻结 + Chunk 强制绑定已生效", flush=True)


def downgrade() -> None:
    # 回滚预检：若存在 failed/cancelled 集合，停止回滚（无法用旧 enum 表示）。
    bind = op.get_bind()
    try:
        row = bind.execute(
            sa.text("SELECT count(*) FROM chunk_sets WHERE status IN ('failed', 'cancelled')")
        ).scalar()
    except sa.exc.ProgrammingError:
        row = 0
    if row:
        raise RuntimeError(
            f"[T06] 存在 {row} 个 failed/cancelled 集合，无法用旧 enum 表示；"
            "请先完成数据保留决策后再回滚。"
        )

    # 回滚只移除新增约束/列，保留历史 Chunk 与其 legacy 绑定（不删除历史数据）。
    op.drop_constraint("ck_chunk_profiles_overlap_range", "chunk_profiles", type_="check")
    op.drop_constraint("ck_chunk_profiles_max_tokens_positive", "chunk_profiles", type_="check")

    op.drop_constraint("ck_chunk_sets_version_positive", "chunk_sets", type_="check")
    op.drop_constraint("ck_chunk_sets_legacy_required_fields", "chunk_sets", type_="check")
    op.drop_index("uq_chunk_sets_single_active", table_name="chunk_sets")
    op.drop_constraint("uq_chunk_sets_task_id", "chunk_sets", type_="unique")
    op.drop_constraint("uq_chunk_sets_doc_idem", "chunk_sets", type_="unique")
    op.drop_constraint("uq_chunk_sets_doc_version", "chunk_sets", type_="unique")

    op.drop_index("ix_chunks_doc_set", table_name="chunks")
    op.drop_index("ix_chunks_set_ordinal", table_name="chunks")
    op.drop_constraint("ck_chunks_token_count_positive", "chunks", type_="check")
    op.drop_constraint("ck_chunks_ordinal_nonneg", "chunks", type_="check")
    op.drop_constraint("uq_chunks_set_ordinal", "chunks", type_="unique")
    op.alter_column("chunks", "chunk_set_id", nullable=True)

    op.drop_constraint("fk_chunk_sets_task_id", "chunk_sets", type_="foreignkey")
    for column in (
        "task_id",
        "completed_at",
        "error_message",
        "splitter_version",
        "output_sha256",
        "source_sha256",
        "idempotency_key",
        "is_legacy",
        "version",
    ):
        op.drop_column("chunk_sets", column)

    # 枚举回退：重建不含 failed/cancelled 的旧枚举（先移除默认值避免依赖阻止）。
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS chunk_set_status")
    op.execute(
        "CREATE TYPE chunk_set_status AS ENUM "
        "('pending', 'processing', 'review_pending', 'completed', 'rejected')"
    )
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status TYPE chunk_set_status USING status::chunk_set_status")
    op.execute("ALTER TABLE chunk_sets ALTER COLUMN status SET DEFAULT 'pending'::chunk_set_status")
