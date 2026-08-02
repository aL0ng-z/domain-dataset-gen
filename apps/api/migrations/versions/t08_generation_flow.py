"""T08: 生成链路修复 - 批次/run 关联、快照冻结、provenance 与 retry 链

Revision ID: t08_generation_flow
Revises: t06_versioned_chunking
Create Date: 2026-08-03

合同要点（对齐 T08 §4）：
- generation_batches 增加：retry_of_generation_batch_id（RESTRICT，线性后继唯一）、
  prompt_template_version_id + 模板/模型 snapshot + sha256 + renderer_version、
  is_legacy/provenance_status/provenance_error_code、completed_at。
- generation_batches/chunk_set/model_config/prompt_template 既有 nullable 列保留
  （legacy 历史行），非 legacy 必填由 CHECK 强制。
- generation_batch_status 枚举增加 cancelled（整体重建：PG 不允许事务内
  ALTER TYPE ADD VALUE 后立刻使用新值）。
- generation_runs 增加：generation_batch_id FK + (batch, chunk) 唯一、
  rendered_prompt_sha256、is_legacy/provenance_status/provenance_error_code。
- generation_run_status 枚举增加 cancelled。
- candidates.generation_run_id 唯一约束（先预检重复，命中停止条件不静默删除）。
- prompt_template_versions (template_id, version) 唯一约束（先预检重复）。
- legacy 回填：迁移前 Batch/Run 先标记 is_legacy=true；只有能从原行、不可变
  PromptTemplateVersion、精确 input_prompt 和关联配置审计数据独立重建并通过
  全部 hash/归属校验的行才回填 verified；缺少必要输入标记 legacy_unavailable；
  发现互相矛盾的 FK、版本或内容标记 invalid。绝不从当前模板/模型配置猜测历史快照。
- 建立 CHECK/trigger 前完成回填。输出三种 provenance 数量及非敏感错误码。
- downgrade 预检：若存在非 legacy 或 cancelling 状态则停止回滚。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t08_generation_flow"
down_revision: str | Sequence[str] | None = "t06_versioned_chunking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rebuild_batch_status_enum() -> None:
    """把 generation_batch_status 枚举整体重建为 6 值（含 cancelled）。"""
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS generation_batch_status")
    op.execute(
        "CREATE TYPE generation_batch_status AS ENUM "
        "('pending', 'processing', 'review_pending', 'completed', 'failed', 'cancelled')"
    )
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status TYPE generation_batch_status USING status::generation_batch_status")
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status SET DEFAULT 'pending'::generation_batch_status")


def _rebuild_run_status_enum() -> None:
    """把 generation_run_status 枚举整体重建为 5 值（含 cancelled）。"""
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS generation_run_status")
    op.execute(
        "CREATE TYPE generation_run_status AS ENUM "
        "('queued', 'processing', 'completed', 'failed', 'cancelled')"
    )
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status TYPE generation_run_status USING status::generation_run_status")
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status SET DEFAULT 'queued'::generation_run_status")


def _count(conn, sql: str, **params) -> int:
    row = conn.execute(sa.text(sql), params).scalar()
    return int(row or 0)


# ---------------------------------------------------------------------------
# provenance 分类（纯函数，供迁移回填与验收测试直接导入/复用）。
# 诚实规则（任务卡 §4）：只有能从原行 + 不可变 PromptTemplateVersion + 精确
# input_prompt + 关联配置审计数据独立重建并通过全部 hash/归属校验的行才可
# verified；缺少必要输入 -> legacy_unavailable；发现互相矛盾的 FK/版本/内容
# -> invalid。绝不从当前模板/模型配置猜测历史快照。
# ---------------------------------------------------------------------------


def _safe_model_config_extra(extra_params) -> bool:
    """model_config extra_params 是否可安全快照（无秘密/不可分类字段）。

    若无法分类（含秘密键），Batch 无法冻结模型快照 -> 不可 verified。
    """
    if extra_params is None:
        return True
    if not isinstance(extra_params, dict):
        return False
    secret_terms = ("api", "token", "secret", "credential", "auth", "password", "key", "header")
    allowed = {"response_format", "stop", "seed", "top_p", "frequency_penalty",
               "presence_penalty", "user", "n", "timeout_seconds", "retries"}
    for key, _value in extra_params.items():
        nk = str(key).replace("-", "").replace("_", "").lower()
        if any(t in nk for t in secret_terms):
            return False
        if nk not in {k.replace("_", "").lower() for k in allowed}:
            return False
    return True


def classify_batch_provenance(
    *,
    selected_chunk_ids,
    total_chunks: int,
    prompt_template_id,
    model_config_id,
    template_version_exists: bool,
    template_content_matches_version: bool,
    model_config_extra_safe: bool,
    has_attributable_run: bool,
) -> str:
    """分类迁移前 Batch 的 provenance。

    参数含义：
    - template_version_exists: 当前版本能否在 prompt_template_versions 精确物化
    - template_content_matches_version: PromptTemplate 当前内容是否与该版本行一致
    - model_config_extra_safe: model_config extra_params 是否可安全快照
    - has_attributable_run: 是否存在可归属到该 Batch 的 GenerationRun

    语义（任务卡 §4）：缺少必要输入 -> legacy_unavailable；发现互相矛盾的
    FK/版本/内容 -> invalid。缺失与矛盾严格区分，绝不猜测。
    """
    # 缺少输入：无选择集/空选择集/无配置引用/版本无法物化/模型无法快照/无关联 run。
    if not selected_chunk_ids or not isinstance(selected_chunk_ids, list):
        return "legacy_unavailable"
    if prompt_template_id is None or model_config_id is None:
        return "legacy_unavailable"
    if not template_version_exists:
        return "legacy_unavailable"
    if not model_config_extra_safe:
        return "legacy_unavailable"
    if not has_attributable_run:
        return "legacy_unavailable"
    # 矛盾：选择集长度与 total 冲突、模板当前内容与版本行不一致。
    if len(selected_chunk_ids) != total_chunks:
        return "invalid"
    if not template_content_matches_version:
        return "invalid"
    return "verified"


def classify_run_provenance(
    *,
    chunk_id,
    input_prompt,
    batch_provenance: str,
    chunk_in_batch_selected: bool,
) -> str:
    """分类迁移前 Run 的 provenance。

    - 所属 Batch 非 verified 或 run 缺 chunk/input_prompt -> legacy_unavailable
      （缺少必要输入，无法独立重建 hash）。
    - Batch verified 但 run 的 chunk 不属于选择集 -> invalid（归属矛盾）。
    """
    if batch_provenance != "verified":
        return "legacy_unavailable"
    if chunk_id is None or input_prompt is None or input_prompt == "":
        return "legacy_unavailable"
    if not chunk_in_batch_selected:
        return "invalid"
    return "verified"


def _backfill_legacy_batches(conn) -> tuple[int, int, int]:
    """迁移前 Batch 的 provenance 回填（Python 逐行审计，诚实 fail closed）。

    返回 (verified, legacy_unavailable, invalid) 数量。
    """
    # 1. 迁移前 Batch 一律先标记 is_legacy=true + legacy_unavailable（默认）。
    op.execute(
        sa.text(
            """
            UPDATE generation_batches
               SET is_legacy = true
                 , provenance_status = 'legacy_unavailable'
                 , provenance_error_code = 'LEGACY_NO_SNAPSHOT'
             WHERE is_legacy = false
            """
        )
    )

    # 2. 逐行分类：需要模板版本/模型配置/可归属 run 数据。
    rows = conn.execute(
        sa.text(
            """
            SELECT gb.id
                 , gb.selected_chunk_ids
                 , gb.total_chunks
                 , gb.prompt_template_id
                 , gb.model_config_id
                 , tpl.version AS tpl_version
                 , EXISTS (
                       SELECT 1 FROM prompt_template_versions v
                        WHERE v.template_id = gb.prompt_template_id
                          AND v.version = tpl.version
                   ) AS ver_exists
                 , EXISTS (
                       SELECT 1 FROM prompt_template_versions v
                        WHERE v.template_id = gb.prompt_template_id
                          AND v.version = tpl.version
                          AND v.system_prompt IS NOT DISTINCT FROM tpl.system_prompt
                          AND v.user_prompt_template IS NOT DISTINCT FROM tpl.user_prompt_template
                          AND v.input_schema IS NOT DISTINCT FROM tpl.input_schema
                          AND v.output_schema IS NOT DISTINCT FROM tpl.output_schema
                   ) AS ver_content_match
                 , EXISTS (
                       SELECT 1 FROM model_configs mc
                        WHERE mc.id = gb.model_config_id
                   ) AS mc_exists
                 , EXISTS (
                       SELECT 1 FROM generation_runs r
                        WHERE r.prompt_template_id = gb.prompt_template_id
                          AND r.model_config_id = gb.model_config_id
                          AND r.chunk_id IN (
                              SELECT jsonb_array_elements_text(gb.selected_chunk_ids)::uuid
                          )
                   ) AS has_run
              FROM generation_batches gb
              LEFT JOIN prompt_templates tpl ON tpl.id = gb.prompt_template_id
             WHERE gb.is_legacy = true
            """
        )
    ).fetchall()

    n_verified = 0
    n_unavailable = 0
    n_invalid = 0
    for row in rows:
        model_extra_safe = _mc_extra_safe(conn, row["model_config_id"])
        status = classify_batch_provenance(
            selected_chunk_ids=row["selected_chunk_ids"],
            total_chunks=int(row["total_chunks"] or 0),
            prompt_template_id=row["prompt_template_id"],
            model_config_id=row["model_config_id"],
            template_version_exists=bool(row["ver_exists"]),
            template_content_matches_version=bool(row["ver_content_match"]),
            model_config_extra_safe=model_extra_safe,
            has_attributable_run=bool(row["has_run"]),
        )
        if status == "verified":
            op.execute(
                sa.text(
                    """
                    UPDATE generation_batches
                       SET is_legacy = false
                         , provenance_status = 'verified'
                         , provenance_error_code = NULL
                     WHERE id = :bid
                    """
                ),
                {"bid": row["id"]},
            )
            n_verified += 1
        elif status == "invalid":
            op.execute(
                sa.text(
                    """
                    UPDATE generation_batches
                       SET provenance_status = 'invalid'
                         , provenance_error_code = 'LEGACY_INVALID'
                     WHERE id = :bid
                    """
                ),
                {"bid": row["id"]},
            )
            n_invalid += 1
        else:
            n_unavailable += 1

    return n_verified, n_unavailable, n_invalid


def _mc_extra_safe(conn, model_config_id) -> bool:
    """读取 model_config.extra_params 并判定是否可安全快照。"""
    if model_config_id is None:
        return False
    row = conn.execute(
        sa.text(
            "SELECT extra_params FROM model_configs WHERE id = :id"
        ),
        {"id": model_config_id},
    ).first()
    if row is None:
        return False
    return _safe_model_config_extra(row["extra_params"])


def _backfill_legacy_runs(conn) -> tuple[int, int, int]:
    """迁移前 Run 的 provenance 回填（Python 逐行审计）。

    返回 (verified, legacy_unavailable, invalid) 数量。
    """
    # 1. 迁移前 Run 一律先标记 is_legacy=true + legacy_unavailable（默认）。
    op.execute(
        sa.text(
            """
            UPDATE generation_runs
               SET is_legacy = true
                 , provenance_status = 'legacy_unavailable'
                 , provenance_error_code = 'LEGACY_NO_INPUT_PROMPT'
             WHERE is_legacy = false
            """
        )
    )

    # 2. 迁移前 run 无 generation_batch_id（旧字段不存在）。要独立重建归属，
    #    需要找到它所属的 Batch（按 chunk_id + template + model 匹配）。
    rows = conn.execute(
        sa.text(
            """
            SELECT r.id
                 , r.chunk_id
                 , r.input_prompt
                 , b.provenance_status AS batch_provenance
                 , b.selected_chunk_ids AS batch_selected
              FROM generation_runs r
              LEFT JOIN generation_batches b
                ON b.prompt_template_id = r.prompt_template_id
               AND b.model_config_id = r.model_config_id
               AND r.chunk_id IN (SELECT jsonb_array_elements_text(b.selected_chunk_ids)::uuid)
             WHERE r.is_legacy = true
            """
        )
    ).fetchall()

    n_verified = 0
    n_unavailable = 0
    n_invalid = 0
    seen: set = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        batch_provenance = row["batch_provenance"]
        if batch_provenance is None:
            batch_provenance = "legacy_unavailable"
        chunk_in_selected = bool(
            row["batch_selected"]
            and row["chunk_id"] is not None
            and any(
                str(row["chunk_id"]) == str(uid)
                for uid in row["batch_selected"]
            )
        )
        status = classify_run_provenance(
            chunk_id=row["chunk_id"],
            input_prompt=row["input_prompt"],
            batch_provenance=batch_provenance,
            chunk_in_batch_selected=chunk_in_selected,
        )
        if status == "verified":
            op.execute(
                sa.text(
                    """
                    UPDATE generation_runs
                       SET is_legacy = false
                         , provenance_status = 'verified'
                         , provenance_error_code = NULL
                     WHERE id = :rid
                    """
                ),
                {"rid": row["id"]},
            )
            n_verified += 1
        elif status == "invalid":
            op.execute(
                sa.text(
                    """
                    UPDATE generation_runs
                       SET provenance_status = 'invalid'
                         , provenance_error_code = 'LEGACY_INVALID'
                     WHERE id = :rid
                    """
                ),
                {"rid": row["id"]},
            )
            n_invalid += 1
        else:
            n_unavailable += 1

    return n_verified, n_unavailable, n_invalid


def _precheck_unique(conn, sql: str, what: str) -> None:
    """加唯一约束前预检重复；命中重复触发停止条件，不静默删 Candidate/Run。"""
    dup = _count(conn, sql)
    if dup:
        raise RuntimeError(
            f"[t08_generation_flow] 预检失败：{what} 存在 {dup} 条重复；"
            f"需要先做数据处置决定，禁止静默删除（任务卡 §12 停止条件）"
        )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. 枚举：generation_batch_status / generation_run_status 增加 cancelled。
    # ------------------------------------------------------------------
    _rebuild_batch_status_enum()
    _rebuild_run_status_enum()

    # ------------------------------------------------------------------
    # 2. generation_batches 扩展列。
    # ------------------------------------------------------------------
    op.add_column("generation_batches", sa.Column("retry_of_generation_batch_id", sa.UUID(), nullable=True))
    op.add_column("generation_batches", sa.Column("prompt_template_version_id", sa.UUID(), nullable=True))
    op.add_column("generation_batches", sa.Column("prompt_template_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("generation_batches", sa.Column("prompt_template_sha256", sa.String(length=64), nullable=True))
    op.add_column("generation_batches", sa.Column("model_config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("generation_batches", sa.Column("model_config_sha256", sa.String(length=64), nullable=True))
    op.add_column("generation_batches", sa.Column("renderer_version", sa.String(length=100), nullable=True))
    op.add_column("generation_batches", sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("generation_batches", sa.Column("provenance_status", sa.String(length=30), nullable=False, server_default="verified"))
    op.add_column("generation_batches", sa.Column("provenance_error_code", sa.String(length=80), nullable=True))
    op.add_column("generation_batches", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "fk_generation_batches_retry_of", "generation_batches",
        "generation_batches", ["retry_of_generation_batch_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_generation_batches_ptv", "generation_batches",
        "prompt_template_versions", ["prompt_template_version_id"], ["id"],
        ondelete="RESTRICT",
    )

    # ------------------------------------------------------------------
    # 3. generation_runs 扩展列。
    # ------------------------------------------------------------------
    op.add_column("generation_runs", sa.Column("generation_batch_id", sa.UUID(), nullable=True))
    op.add_column("generation_runs", sa.Column("rendered_prompt_sha256", sa.String(length=64), nullable=True))
    op.add_column("generation_runs", sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("generation_runs", sa.Column("provenance_status", sa.String(length=30), nullable=False, server_default="verified"))
    op.add_column("generation_runs", sa.Column("provenance_error_code", sa.String(length=80), nullable=True))

    op.create_foreign_key(
        "fk_generation_runs_batch", "generation_runs",
        "generation_batches", ["generation_batch_id"], ["id"],
        ondelete="RESTRICT",
    )

    # ------------------------------------------------------------------
    # 4. legacy 回填（建立约束/trigger 前）。
    # ------------------------------------------------------------------
    n_b_verified, n_b_unavailable, n_b_invalid = _backfill_legacy_batches(bind)
    n_r_verified, n_r_unavailable, n_r_invalid = _backfill_legacy_runs(bind)
    print(
        f"[t08_generation_flow] Batch provenance: verified={n_b_verified} "
        f"legacy_unavailable={n_b_unavailable} invalid={n_b_invalid}"
    )
    print(
        f"[t08_generation_flow] Run provenance: verified={n_r_verified} "
        f"legacy_unavailable={n_r_unavailable} invalid={n_r_invalid}"
    )

    # ------------------------------------------------------------------
    # 5. 唯一约束（加前预检重复；命中停止条件）。
    # ------------------------------------------------------------------
    _precheck_unique(
        bind,
        """
        SELECT count(*) FROM (
            SELECT generation_run_id FROM candidates
             WHERE generation_run_id IS NOT NULL
             GROUP BY generation_run_id HAVING count(*) > 1
        ) d
        """,
        "candidates.generation_run_id",
    )
    op.create_unique_constraint(
        "uq_candidates_generation_run", "candidates", ["generation_run_id"]
    )

    _precheck_unique(
        bind,
        """
        SELECT count(*) FROM (
            SELECT template_id, version FROM prompt_template_versions
             GROUP BY template_id, version HAVING count(*) > 1
        ) d
        """,
        "prompt_template_versions.(template_id, version)",
    )
    op.create_unique_constraint(
        "uq_prompt_template_versions_tpl_version", "prompt_template_versions", ["template_id", "version"]
    )

    _precheck_unique(
        bind,
        """
        SELECT count(*) FROM (
            SELECT retry_of_generation_batch_id FROM generation_batches
             WHERE retry_of_generation_batch_id IS NOT NULL
             GROUP BY retry_of_generation_batch_id HAVING count(*) > 1
        ) d
        """,
        "generation_batches.retry_of_generation_batch_id",
    )
    op.create_unique_constraint(
        "uq_generation_batches_single_retry_successor", "generation_batches", ["retry_of_generation_batch_id"]
    )

    _precheck_unique(
        bind,
        """
        SELECT count(*) FROM (
            SELECT generation_batch_id, chunk_id FROM generation_runs
             WHERE generation_batch_id IS NOT NULL
             GROUP BY generation_batch_id, chunk_id HAVING count(*) > 1
        ) d
        """,
        "generation_runs.(generation_batch_id, chunk_id)",
    )
    op.create_unique_constraint(
        "uq_generation_runs_batch_chunk", "generation_runs", ["generation_batch_id", "chunk_id"]
    )

    # ------------------------------------------------------------------
    # 6. CHECK 约束（回填后建立，避免迁移前旧行违反）。
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "ck_generation_batches_chunk_counts", "generation_batches",
        "total_chunks >= 0 AND completed_chunks >= 0 AND completed_chunks <= total_chunks",
    )
    op.create_check_constraint(
        "ck_generation_batches_total_matches_selected", "generation_batches",
        "is_legacy = true OR (selected_chunk_ids IS NOT NULL "
        "AND jsonb_array_length(selected_chunk_ids) = total_chunks)",
    )
    op.create_check_constraint(
        "ck_generation_batches_required_not_legacy", "generation_batches",
        "is_legacy = true OR (chunk_set_id IS NOT NULL AND model_config_id IS NOT NULL "
        "AND prompt_template_id IS NOT NULL AND selected_chunk_ids IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_generation_batches_verified_not_legacy", "generation_batches",
        "is_legacy = true OR provenance_status = 'verified'",
    )
    op.create_check_constraint(
        "ck_generation_batches_verified_has_snapshot", "generation_batches",
        "provenance_status != 'verified' OR (prompt_template_version_id IS NOT NULL "
        "AND prompt_template_snapshot IS NOT NULL AND prompt_template_sha256 IS NOT NULL "
        "AND model_config_snapshot IS NOT NULL AND model_config_sha256 IS NOT NULL "
        "AND renderer_version IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_generation_batches_legacy_unavailable_error", "generation_batches",
        "provenance_status NOT IN ('legacy_unavailable', 'invalid') OR "
        "(is_legacy = true AND provenance_error_code IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_generation_batches_provenance_status_valid", "generation_batches",
        "provenance_status IN ('verified', 'legacy_unavailable', 'invalid')",
    )
    op.create_check_constraint(
        "ck_generation_batches_terminal_completed_at", "generation_batches",
        "is_legacy = true OR (status IN ('completed', 'failed', 'cancelled') = (completed_at IS NOT NULL))",
    )
    op.create_index(
        "ix_generation_batches_document_created", "generation_batches",
        ["document_id", sa.text("created_at DESC")],
    )

    op.create_check_constraint(
        "ck_generation_runs_verified_not_legacy", "generation_runs",
        "is_legacy = true OR provenance_status = 'verified'",
    )
    op.create_check_constraint(
        "ck_generation_runs_verified_has_input", "generation_runs",
        "provenance_status != 'verified' OR (generation_batch_id IS NOT NULL "
        "AND input_prompt IS NOT NULL AND rendered_prompt_sha256 IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_generation_runs_legacy_unavailable_error", "generation_runs",
        "provenance_status NOT IN ('legacy_unavailable', 'invalid') OR "
        "(is_legacy = true AND provenance_error_code IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_generation_runs_provenance_status_valid", "generation_runs",
        "provenance_status IN ('verified', 'legacy_unavailable', 'invalid')",
    )
    op.create_check_constraint(
        "ck_generation_runs_terminal_completed_at", "generation_runs",
        "is_legacy = true OR (status IN ('completed', 'failed', 'cancelled') = (completed_at IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_generation_runs_failed_has_error", "generation_runs",
        "is_legacy = true OR (status != 'failed' OR error_message IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_generation_runs_completed_has_output", "generation_runs",
        "is_legacy = true OR (status != 'completed' OR raw_output IS NOT NULL)",
    )

    # 受控 trigger/service 保证 verified Run 所属 Batch 也是 verified：
    # 数据库 trigger 在 batch 变为非 verified 时阻止，或在 run 标 verified 时
    # 校验所属 batch verified。此处建立跨表 trigger。
    _create_verified_run_trigger(bind)


def _create_verified_run_trigger(conn) -> None:
    """verified Run 所属 Batch 必须也是 verified（跨表 trigger）。

    在 generation_runs 上：INSERT/UPDATE 时若新行 provenance_status='verified'，
    校验所属 generation_batch 的 provenance_status='verified'；否则抛异常。
    """
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_generation_runs_batch_verified() RETURNS trigger AS $$
            BEGIN
                IF NEW.provenance_status = 'verified' AND NEW.generation_batch_id IS NOT NULL THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM generation_batches b
                         WHERE b.id = NEW.generation_batch_id
                           AND b.provenance_status = 'verified'
                    ) THEN
                        RAISE EXCEPTION 'verified run must belong to verified batch';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DROP TRIGGER IF EXISTS trg_generation_runs_batch_verified ON generation_runs
            """
        )
    )
    conn.execute(
        sa.text(
            """
            CREATE TRIGGER trg_generation_runs_batch_verified
            BEFORE INSERT OR UPDATE OF provenance_status, generation_batch_id
            ON generation_runs
            FOR EACH ROW EXECUTE FUNCTION trg_generation_runs_batch_verified()
            """
        )
    )


def downgrade() -> None:
    # 回滚预检：存在非 legacy 或 cancelled 状态的新格式数据则停止。
    bind = op.get_bind()
    try:
        nonlegacy = _count(
            bind,
            "SELECT count(*) FROM generation_batches WHERE is_legacy = false",
        )
        cancelled_b = _count(
            bind,
            "SELECT count(*) FROM generation_batches WHERE status = 'cancelled'",
        )
        cancelled_r = _count(
            bind,
            "SELECT count(*) FROM generation_runs WHERE status = 'cancelled'",
        )
    except sa.exc.ProgrammingError:
        nonlegacy = cancelled_b = cancelled_r = 0
    if nonlegacy or cancelled_b or cancelled_r:
        raise RuntimeError(
            f"[t08_generation_flow] 回滚预检失败：存在非 legacy Batch（{nonlegacy}）"
            f"或 cancelled 状态（batch={cancelled_b}, run={cancelled_r}）；"
            f"请先停止 worker 并清理新格式数据后再回滚。"
        )

    op.execute("DROP TRIGGER IF EXISTS trg_generation_runs_batch_verified ON generation_runs")
    op.execute("DROP FUNCTION IF EXISTS trg_generation_runs_batch_verified()")

    for constraint in (
        "ck_generation_runs_completed_has_output",
        "ck_generation_runs_failed_has_error",
        "ck_generation_runs_terminal_completed_at",
        "ck_generation_runs_provenance_status_valid",
        "ck_generation_runs_legacy_unavailable_error",
        "ck_generation_runs_verified_has_input",
        "ck_generation_runs_verified_not_legacy",
    ):
        op.drop_constraint(constraint, "generation_runs", type_="check")
    for constraint in (
        "ck_generation_batches_terminal_completed_at",
        "ck_generation_batches_provenance_status_valid",
        "ck_generation_batches_legacy_unavailable_error",
        "ck_generation_batches_verified_has_snapshot",
        "ck_generation_batches_verified_not_legacy",
        "ck_generation_batches_required_not_legacy",
        "ck_generation_batches_total_matches_selected",
        "ck_generation_batches_chunk_counts",
    ):
        op.drop_constraint(constraint, "generation_batches", type_="check")

    op.drop_index("ix_generation_batches_document_created", table_name="generation_batches")

    op.drop_constraint("uq_generation_runs_batch_chunk", "generation_runs", type_="unique")
    op.drop_constraint("uq_generation_batches_single_retry_successor", "generation_batches", type_="unique")
    op.drop_constraint("uq_prompt_template_versions_tpl_version", "prompt_template_versions", type_="unique")
    op.drop_constraint("uq_candidates_generation_run", "candidates", type_="unique")

    op.drop_constraint("fk_generation_runs_batch", "generation_runs", type_="foreignkey")
    op.drop_constraint("fk_generation_batches_ptv", "generation_batches", type_="foreignkey")
    op.drop_constraint("fk_generation_batches_retry_of", "generation_batches", type_="foreignkey")

    for column in (
        "provenance_error_code",
        "provenance_status",
        "is_legacy",
        "rendered_prompt_sha256",
        "generation_batch_id",
    ):
        op.drop_column("generation_runs", column)
    for column in (
        "completed_at",
        "provenance_error_code",
        "provenance_status",
        "is_legacy",
        "renderer_version",
        "model_config_sha256",
        "model_config_snapshot",
        "prompt_template_sha256",
        "prompt_template_snapshot",
        "prompt_template_version_id",
        "retry_of_generation_batch_id",
    ):
        op.drop_column("generation_batches", column)

    # 枚举回退：batch 重建为 5 值（无 cancelled），run 重建为 4 值。
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS generation_batch_status")
    op.execute(
        "CREATE TYPE generation_batch_status AS ENUM "
        "('pending', 'processing', 'review_pending', 'completed', 'failed')"
    )
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status TYPE generation_batch_status USING status::generation_batch_status")
    op.execute("ALTER TABLE generation_batches ALTER COLUMN status SET DEFAULT 'pending'::generation_batch_status")

    op.execute("ALTER TABLE generation_runs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status TYPE VARCHAR(50)")
    op.execute("DROP TYPE IF EXISTS generation_run_status")
    op.execute(
        "CREATE TYPE generation_run_status AS ENUM ('queued', 'processing', 'completed', 'failed')"
    )
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status TYPE generation_run_status USING status::generation_run_status")
    op.execute("ALTER TABLE generation_runs ALTER COLUMN status SET DEFAULT 'queued'::generation_run_status")
