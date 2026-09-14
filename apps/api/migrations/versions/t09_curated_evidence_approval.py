"""T09: Candidate/CuratedItem 证据与审批 - 审核状态收敛、revision/审批指针与不可变绑定

Revision ID: t09_candidate_curated_evidence_approval
Revises: t08_generation_flow
Create Date: 2026-08-03

合同要点（对齐 T09 §4.1/§4.2）：
- candidates 删除重复的 review_status 列；status 成为唯一审核状态源。
  加约束前预检：任何非默认 review_status（!= 'pending'）即触发停止条件，
  不猜测历史审核结论。
- curated_items.candidate_id 唯一约束（加前预检重复，命中停止条件不静默删除）。
- curated_items 增加 current_revision/approved_revision_id/approval_record_id/
  approved_by/approved_at；approved 当且仅当四者全非空（CHECK + deferred trigger）。
- curated_revisions 增加 version/content_sha256/canonicalization_version 与
  (item, version) 唯一；revision 内容为完整 JSON 快照，插入后不可 UPDATE/DELETE。
- evidence_links 增加 start_char/end_char（Unicode code point 精确坐标）与
  (item, chunk, start, end) 唯一；历史无 quote 无法精确回填的证据链接删除，
  不伪造缺失 quote（任务卡 §12 诚实回填）。
- review_records 增加审批绑定列：entity_revision_id/revision_content_sha256/
  evidence_snapshot/evidence_sha256/canonicalization_version；CuratedItem 审批
  记录插入后不可更新/删除。
- 诚实回填：历史 CuratedRevision 按 created_at 分配 version 并计算
  curated-content-cjson-v1 内容 hash；无 revision 的 CuratedItem 补 v1；
  历史 approved 但无审批记录/精确证据的条目全部退回 draft（绝不自动设 approved）。
- 跨表 deferred constraint trigger：approved 时必须通过
  approved_revision_id / approval_record_id 同属当前 item、approval record 绑定
  同一 revision、revision content hash 一致、canonicalization version 已知。
- downgrade 预检：存在已绑定审批记录的数据则停止回滚。
"""
from __future__ import annotations

import json
import sys
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# domain.canonical 是 hash 可跨端重算的唯一事实源（任务卡 §4.2/§11 验收 11）。
# 迁移可能直接从 apps/api 目录运行（无 PYTHONPATH），这里按文件位置补 libs/domain。
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (_REPO_ROOT / "libs" / "domain",):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from domain.canonical import (  # noqa: E402
    CURATED_APPROVAL_CJSON_VERSION,
    CURATED_CONTENT_CJSON_VERSION,
    curated_content_sha256,
)

revision: str = "t09_curated_evidence_approval"
down_revision: str | Sequence[str] | None = "t08_generation_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _count(conn, sql: str, **params) -> int:
    row = conn.execute(sa.text(sql), params).scalar()
    return int(row or 0)


def _precheck_unique(conn, sql: str, what: str) -> None:
    """加唯一约束前预检重复；命中重复触发停止条件，不静默删除/合并。"""
    dup = _count(conn, sql)
    if dup:
        raise RuntimeError(
            f"[t09] 预检失败：{what} 存在 {dup} 条重复；"
            f"需要先做数据处置批准，禁止静默删除或合并（任务卡 §12 停止条件）"
        )


def _precheck_review_status(conn) -> None:
    """预检 review_status 权威来源；任何非默认值即停止，不猜测审核结论。"""
    divergent = _count(
        conn,
        "SELECT count(*) FROM candidates WHERE review_status IS DISTINCT FROM 'pending'",
    )
    if divergent:
        raise RuntimeError(
            f"[t09] 预检失败：{divergent} 条 Candidate 的 review_status 非默认 "
            f"pending；无法确定 status/review_status 权威来源且迁移会改变已审核结论，"
            f"命中停止条件，禁止删除 review_status 列"
        )


def _backfill_curated_revisions(conn) -> int:
    """为历史 CuratedRevision 分配 version 并计算内容 hash（诚实回填）。

    返回 revision 总数。version 按 (created_at, id) 在每个 item 内从 1 递增；
    content_sha256 对 curated-content-cjson-v1 规范字节计算，可跨端重算。
    """
    rows = conn.execute(
        sa.text(
            "SELECT id, curated_item_id, content FROM curated_revisions ORDER BY created_at, id"
        )
    ).mappings().all()
    by_item: dict = {}
    for row in rows:
        by_item.setdefault(row["curated_item_id"], []).append(row)

    total = 0
    for revs in by_item.values():
        for idx, rev in enumerate(revs, start=1):
            content_hash = curated_content_sha256(dict(rev["content"] or {}))
            conn.execute(
                sa.text(
                    "UPDATE curated_revisions SET version = :v, content_sha256 = :h, "
                    "canonicalization_version = :cv WHERE id = :rid"
                ),
                {
                    "v": idx,
                    "h": content_hash,
                    "cv": CURATED_CONTENT_CJSON_VERSION,
                    "rid": rev["id"],
                },
            )
            total += 1
    return total


def _backfill_curated_items(conn) -> tuple[int, int]:
    """为历史 CuratedItem 补齐 v1 revision 并设置 current_revision。

    返回 (创建 v1 的条目数, 设置 current_revision 的条目数)。
    无 revision 的条目以 item.content 创建 version=1 revision（revised_by=promoted_by），
    content hash 对 canonical 字节计算；绝不伪造证据或自动设 approved。
    """
    items = conn.execute(
        sa.text(
            """
            SELECT ci.id, ci.content, ci.promoted_by,
                   (SELECT max(r.version) FROM curated_revisions r
                     WHERE r.curated_item_id = ci.id) AS max_version
              FROM curated_items ci
            """
        )
    ).mappings().all()

    n_created_v1 = 0
    n_set_current = 0
    for item in items:
        max_version = item["max_version"] or 0
        if max_version == 0:
            content_hash = curated_content_sha256(dict(item["content"] or {}))
            conn.execute(
                sa.text(
                    "INSERT INTO curated_revisions "
                    "(id, curated_item_id, revised_by, version, content, content_sha256, "
                    " canonicalization_version, created_at) "
                    "VALUES (:id, :iid, :uid, 1, CAST(:content AS jsonb), :h, :cv, :ts)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "iid": item["id"],
                    "uid": item["promoted_by"],
                    "content": json.dumps(item["content"] or {}, ensure_ascii=False),
                    "h": content_hash,
                    "cv": CURATED_CONTENT_CJSON_VERSION,
                    "ts": datetime.now(UTC),
                },
            )
            n_created_v1 += 1
            current = 1
        else:
            current = int(max_version)
        conn.execute(
            sa.text("UPDATE curated_items SET current_revision = :c WHERE id = :iid"),
            {"c": current, "iid": item["id"]},
        )
        n_set_current += 1
    return n_created_v1, n_set_current


def _find_quote_span_in_original(content: str, quote: str) -> tuple[int, int] | None:
    """返回 NFC 等价 quote 在原始字符串中的 code point 边界。

    迁移持久化的是原始 ``Chunk.content`` 的坐标，不能把 NFC 字符串的位置直接
    写回。先走精确查找；只在需要 NFC 等价匹配时将规范化片段映射回原始边界。
    """
    direct = content.find(quote)
    if direct >= 0:
        return direct, direct + len(quote)

    quote_nfc = unicodedata.normalize("NFC", quote)
    if not quote_nfc:
        return None

    normalized_parts: list[str] = []
    normalized_starts: list[int] = []
    normalized_ends: list[int] = []
    unit_start = 0
    for index in range(1, len(content) + 1):
        # 一个基础字符及其后续 combining marks 在 NFC 后共享同一原始边界。
        if index < len(content) and unicodedata.combining(content[index]):
            continue
        normalized = unicodedata.normalize("NFC", content[unit_start:index])
        normalized_parts.append(normalized)
        normalized_starts.extend([unit_start] * len(normalized))
        normalized_ends.extend([index] * len(normalized))
        unit_start = index

    normalized_content = "".join(normalized_parts)
    search_from = 0
    while True:
        index = normalized_content.find(quote_nfc, search_from)
        if index < 0:
            return None
        end_index = index + len(quote_nfc)
        start = normalized_starts[index]
        end = normalized_ends[end_index - 1]
        if unicodedata.normalize("NFC", content[start:end]) == quote_nfc:
            return start, end
        search_from = index + 1


def _backfill_evidence_offsets(conn) -> tuple[int, int]:
    """为历史 EvidenceLink 精确回填 start/end；无法精确回填的证据删除。

    返回 (成功回填, 删除) 数量。
    - quote_text 精确命中 chunk.content（按 Unicode code point）-> 写入 start/end。
    - quote_text 为空或无法在 chunk 原文定位 -> 删除该证据链接（不得伪造缺失
      quote）。其所属 CuratedItem 由 _demote_approved_items 统一降级。
    """
    links = conn.execute(
        sa.text(
            """
            SELECT el.id, el.curated_item_id, el.chunk_id, el.quote_text, c.content AS chunk_content
              FROM evidence_links el
              JOIN chunks c ON c.id = el.chunk_id
            """
        )
    ).mappings().all()

    n_kept = 0
    n_deleted = 0
    for link in links:
        quote = link["quote_text"]
        chunk_content = link["chunk_content"] or ""
        span = _find_quote_span_in_original(chunk_content, quote) if quote else None
        if span is None:
            conn.execute(
                sa.text("DELETE FROM evidence_links WHERE id = :id"),
                {"id": link["id"]},
            )
            n_deleted += 1
        else:
            start, end = span
            conn.execute(
                sa.text(
                    "UPDATE evidence_links SET start_char = :s, end_char = :e WHERE id = :id"
                ),
                {"s": start, "e": end, "id": link["id"]},
            )
            n_kept += 1
    return n_kept, n_deleted


def _demote_approved_items(conn) -> int:
    """把无审批记录的 approved CuratedItem 退回 draft（诚实回填）。

    历史条目在旧流程下无审批记录，无法复核 approved revision/证据快照；
    任务卡 §4.2 要求不自动把无证据旧条目设为 approved，故全部退回 draft。
    返回降级条数。
    """
    rows = conn.execute(
        sa.text(
            "SELECT id FROM curated_items WHERE status = 'approved' AND approval_record_id IS NULL"
        )
    ).mappings().all()
    n = 0
    for row in rows:
        conn.execute(
            sa.text("UPDATE curated_items SET status = 'draft' WHERE id = :id"),
            {"id": row["id"]},
        )
        n += 1
    return n


def _create_curated_revisions_immutable_trigger(conn) -> None:
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_curated_revisions_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'curated_revisions are immutable (insert-only)';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_curated_revisions_immutable ON curated_revisions"
        )
    )
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_curated_revisions_immutable "
            "BEFORE UPDATE OR DELETE ON curated_revisions "
            "FOR EACH ROW EXECUTE FUNCTION trg_curated_revisions_immutable()"
        )
    )


def _create_curated_review_record_immutable_trigger(conn) -> None:
    """CuratedItem 的审批/退审记录不可更新/删除（批准事实不可变）。"""
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_review_records_curated_immutable() RETURNS trigger AS $$
            BEGIN
                IF OLD.entity_type = 'curated_item' THEN
                    RAISE EXCEPTION 'curated_item review records are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_review_records_curated_immutable ON review_records"
        )
    )
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_review_records_curated_immutable "
            "BEFORE UPDATE OR DELETE ON review_records "
            "FOR EACH ROW EXECUTE FUNCTION trg_review_records_curated_immutable()"
        )
    )


def _create_curated_item_approval_consistency_trigger(conn) -> None:
    """deferred constraint trigger：approved 时必须通过审批绑定一致性校验。

    校验项（任务卡 §4.2）：
    - approved_revision_id 属于当前 CuratedItem；
    - approval_record_id 是当前 item 的 approve 记录且绑定同一 revision；
    - approval record 的 revision_content_sha256 与被批准 revision 的
      content_sha256 一致；
    - canonicalization version 为已知版本且 evidence_sha256 为合法 SHA-256。
    """
    conn.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION trg_curated_item_approval_consistency() RETURNS trigger AS $$
            DECLARE
                rec_ok boolean;
            BEGIN
                IF NEW.status = 'approved' THEN
                    IF NEW.approved_revision_id IS NULL OR NEW.approval_record_id IS NULL THEN
                        RAISE EXCEPTION 'approved curated item requires approved_revision_id and approval_record_id';
                    END IF;
                    SELECT EXISTS (
                        SELECT 1
                          FROM curated_revisions r
                          JOIN review_records rr
                            ON rr.id = NEW.approval_record_id
                         WHERE r.id = NEW.approved_revision_id
                           AND r.curated_item_id = NEW.id
                           AND rr.entity_type = 'curated_item'
                           AND rr.entity_id = NEW.id
                           AND rr.action = 'approve'
                           AND rr.entity_revision_id = NEW.approved_revision_id
                           AND rr.revision_content_sha256 = r.content_sha256
                           AND rr.canonicalization_version = '{CURATED_APPROVAL_CJSON_VERSION}'
                           AND rr.evidence_sha256 ~ '^[0-9a-f]{{64}}$'
                    ) INTO rec_ok;
                    IF NOT rec_ok THEN
                        RAISE EXCEPTION 'curated item approval binding is inconsistent';
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
            "DROP TRIGGER IF EXISTS trg_curated_item_approval_consistency ON curated_items"
        )
    )
    conn.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER trg_curated_item_approval_consistency "
            "AFTER INSERT OR UPDATE OF status, approved_revision_id, approval_record_id "
            "ON curated_items DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION trg_curated_item_approval_consistency()"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. 预检（停止条件）。
    # ------------------------------------------------------------------
    _precheck_review_status(bind)
    _precheck_unique(
        bind,
        """
        SELECT count(*) FROM (
            SELECT candidate_id FROM curated_items
             GROUP BY candidate_id HAVING count(*) > 1
        ) d
        """,
        "curated_items.candidate_id",
    )

    # ------------------------------------------------------------------
    # 2. curated_revisions 扩展列 + 回填（version/content hash 可跨端重算）。
    # ------------------------------------------------------------------
    op.add_column("curated_revisions", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column("curated_revisions", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.add_column("curated_revisions", sa.Column("canonicalization_version", sa.String(length=50), nullable=True))

    n_revs = _backfill_curated_revisions(bind)
    print(f"[t09] curated_revisions backfill: {n_revs} rows versioned/hashed")

    op.alter_column("curated_revisions", "version", nullable=False)
    op.alter_column("curated_revisions", "content_sha256", nullable=False)
    op.alter_column("curated_revisions", "canonicalization_version", nullable=False)

    # ------------------------------------------------------------------
    # 3. curated_items 扩展列 + 回填。
    # ------------------------------------------------------------------
    op.add_column("curated_items", sa.Column("current_revision", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("curated_items", sa.Column("approved_revision_id", sa.UUID(), nullable=True))
    op.add_column("curated_items", sa.Column("approval_record_id", sa.UUID(), nullable=True))
    op.add_column("curated_items", sa.Column("approved_by", sa.UUID(), nullable=True))
    op.add_column("curated_items", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))

    n_created_v1, n_set_current = _backfill_curated_items(bind)
    print(
        f"[t09] curated_items backfill: created_v1={n_created_v1} "
        f"current_revision_set={n_set_current}"
    )

    # ------------------------------------------------------------------
    # 4. evidence_links 扩展列 + 精确回填（不伪造缺失 quote）。
    # ------------------------------------------------------------------
    op.add_column("evidence_links", sa.Column("start_char", sa.Integer(), nullable=True))
    op.add_column("evidence_links", sa.Column("end_char", sa.Integer(), nullable=True))

    n_kept, n_deleted = _backfill_evidence_offsets(bind)
    print(f"[t09] evidence_links backfill: kept={n_kept} deleted_unverifiable={n_deleted}")

    # 回填后才可预检坐标唯一性（start_char/end_char 此前不存在）。
    _precheck_unique(
        bind,
        """
        SELECT count(*) FROM (
            SELECT curated_item_id, chunk_id, start_char, end_char FROM evidence_links
             GROUP BY curated_item_id, chunk_id, start_char, end_char HAVING count(*) > 1
        ) d
        """,
        "evidence_links.(curated_item_id, chunk_id, start_char, end_char)",
    )

    op.alter_column("evidence_links", "start_char", nullable=False)
    op.alter_column("evidence_links", "end_char", nullable=False)

    # ------------------------------------------------------------------
    # 5. 历史 approved 条目诚实降级（无审批记录 -> draft，绝不自动设 approved）。
    # ------------------------------------------------------------------
    n_demoted = _demote_approved_items(bind)
    print(f"[t09] demoted unverifiable approved items to draft: {n_demoted}")

    # ------------------------------------------------------------------
    # 6. review_records 审批绑定列。
    # ------------------------------------------------------------------
    op.add_column("review_records", sa.Column("entity_revision_id", sa.UUID(), nullable=True))
    op.add_column("review_records", sa.Column("revision_content_sha256", sa.String(length=64), nullable=True))
    op.add_column("review_records", sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("review_records", sa.Column("evidence_sha256", sa.String(length=64), nullable=True))
    op.add_column("review_records", sa.Column("canonicalization_version", sa.String(length=50), nullable=True))

    # ------------------------------------------------------------------
    # 7. 外键与唯一约束（回填后建立）。
    # ------------------------------------------------------------------
    op.create_foreign_key(
        "fk_curated_items_approved_revision", "curated_items",
        "curated_revisions", ["approved_revision_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_curated_items_approval_record", "curated_items",
        "review_records", ["approval_record_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_review_records_entity_revision", "review_records",
        "curated_revisions", ["entity_revision_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_curated_revisions_item_version", "curated_revisions", ["curated_item_id", "version"])
    op.create_unique_constraint("uq_curated_items_candidate", "curated_items", ["candidate_id"])
    op.create_unique_constraint("uq_evidence_links_item_chunk_offsets", "evidence_links", ["curated_item_id", "chunk_id", "start_char", "end_char"])

    # ------------------------------------------------------------------
    # 8. CHECK 约束。
    # ------------------------------------------------------------------
    op.create_check_constraint("ck_curated_revisions_version_positive", "curated_revisions", "version >= 1")
    op.create_check_constraint(
        "ck_curated_revisions_sha256_format", "curated_revisions",
        "content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint("ck_curated_items_revision_positive", "curated_items", "current_revision >= 1")
    op.create_check_constraint(
        "ck_curated_items_approved_complete", "curated_items",
        "status != 'approved' OR (approved_revision_id IS NOT NULL AND approval_record_id IS NOT NULL "
        "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_curated_items_draft_no_approval", "curated_items",
        "status != 'draft' OR (approved_revision_id IS NULL AND approval_record_id IS NULL "
        "AND approved_by IS NULL AND approved_at IS NULL)",
    )
    op.create_check_constraint("ck_evidence_links_start_nonneg", "evidence_links", "start_char >= 0")
    op.create_check_constraint("ck_evidence_links_end_gt_start", "evidence_links", "end_char > start_char")
    op.create_check_constraint(
        "ck_review_records_curated_approve_binding", "review_records",
        "entity_type != 'curated_item' OR action != 'approve' OR ("
        "entity_revision_id IS NOT NULL AND revision_content_sha256 IS NOT NULL "
        "AND evidence_snapshot IS NOT NULL AND evidence_sha256 IS NOT NULL "
        "AND canonicalization_version IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_review_records_curated_non_approve_no_binding", "review_records",
        "entity_type != 'curated_item' OR action = 'approve' OR ("
        "entity_revision_id IS NULL AND revision_content_sha256 IS NULL "
        "AND evidence_snapshot IS NULL AND evidence_sha256 IS NULL "
        "AND canonicalization_version IS NULL)",
    )
    op.create_check_constraint(
        "ck_review_records_sha256_format", "review_records",
        "revision_content_sha256 IS NULL OR revision_content_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_review_records_evidence_sha256_format", "review_records",
        "evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_index("ix_curated_revisions_item_version", "curated_revisions", ["curated_item_id", "version"])

    # ------------------------------------------------------------------
    # 9. 删除重复 review_status 列。
    # ------------------------------------------------------------------
    op.drop_column("candidates", "review_status")

    # ------------------------------------------------------------------
    # 10. 不可变/deferred trigger。
    # ------------------------------------------------------------------
    _create_curated_revisions_immutable_trigger(bind)
    _create_curated_review_record_immutable_trigger(bind)
    _create_curated_item_approval_consistency_trigger(bind)


def downgrade() -> None:
    bind = op.get_bind()
    # 回滚预检：存在已绑定审批的数据则停止（drop 绑定列会丢失批准事实）。
    try:
        approved_with_binding = _count(
            bind,
            """
            SELECT count(*) FROM curated_items
             WHERE approved_revision_id IS NOT NULL OR approval_record_id IS NOT NULL
            """,
        )
        bound_records = _count(
            bind,
            "SELECT count(*) FROM review_records WHERE entity_revision_id IS NOT NULL",
        )
    except sa.exc.ProgrammingError:
        approved_with_binding = bound_records = 0
    if approved_with_binding or bound_records:
        raise RuntimeError(
            f"[t09] 回滚预检失败：存在已绑定审批记录（items={approved_with_binding}, "
            f"records={bound_records}）；请先停止 worker 并清理新格式审批数据后再回滚。"
        )

    op.execute("DROP TRIGGER IF EXISTS trg_curated_item_approval_consistency ON curated_items")
    op.execute("DROP FUNCTION IF EXISTS trg_curated_item_approval_consistency()")
    op.execute("DROP TRIGGER IF EXISTS trg_review_records_curated_immutable ON review_records")
    op.execute("DROP FUNCTION IF EXISTS trg_review_records_curated_immutable()")
    op.execute("DROP TRIGGER IF EXISTS trg_curated_revisions_immutable ON curated_revisions")
    op.execute("DROP FUNCTION IF EXISTS trg_curated_revisions_immutable()")

    # 恢复 review_status 列（诚实回填为默认 pending）。
    op.add_column(
        "candidates",
        sa.Column("review_status", sa.String(30), nullable=False, server_default="pending"),
    )

    op.drop_index("ix_curated_revisions_item_version", table_name="curated_revisions")
    for table, constraint in (
        ("review_records", "ck_review_records_evidence_sha256_format"),
        ("review_records", "ck_review_records_sha256_format"),
        ("review_records", "ck_review_records_curated_non_approve_no_binding"),
        ("review_records", "ck_review_records_curated_approve_binding"),
        ("evidence_links", "ck_evidence_links_end_gt_start"),
        ("evidence_links", "ck_evidence_links_start_nonneg"),
        ("curated_items", "ck_curated_items_draft_no_approval"),
        ("curated_items", "ck_curated_items_approved_complete"),
        ("curated_items", "ck_curated_items_revision_positive"),
        ("curated_revisions", "ck_curated_revisions_sha256_format"),
        ("curated_revisions", "ck_curated_revisions_version_positive"),
    ):
        op.drop_constraint(constraint, table, type_="check")

    op.drop_constraint("uq_evidence_links_item_chunk_offsets", "evidence_links", type_="unique")
    op.drop_constraint("uq_curated_items_candidate", "curated_items", type_="unique")
    op.drop_constraint("uq_curated_revisions_item_version", "curated_revisions", type_="unique")

    op.drop_constraint("fk_review_records_entity_revision", "review_records", type_="foreignkey")
    op.drop_constraint("fk_curated_items_approval_record", "curated_items", type_="foreignkey")
    op.drop_constraint("fk_curated_items_approved_revision", "curated_items", type_="foreignkey")

    for column in (
        "canonicalization_version",
        "evidence_sha256",
        "evidence_snapshot",
        "revision_content_sha256",
        "entity_revision_id",
    ):
        op.drop_column("review_records", column)
    for column in ("end_char", "start_char"):
        op.drop_column("evidence_links", column)
    for column in ("approved_at", "approved_by", "approval_record_id", "approved_revision_id", "current_revision"):
        op.drop_column("curated_items", column)
    for column in ("canonicalization_version", "content_sha256", "version"):
        op.drop_column("curated_revisions", column)
