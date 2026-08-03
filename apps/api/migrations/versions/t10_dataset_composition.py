"""T10: Dataset/Benchmark composition - 固定批准 revision 的编组、唯一约束、canonical hash 与 finalize

Revision ID: t10_dataset_composition
Revises: t09_curated_evidence_approval
Create Date: 2026-08-03

合同要点（对齐 T10 §4 数据库合同）：
- dataset_items / benchmark_cases 增加唯一约束 (container, curated_item_id) 与
  (container, ordinal)；ordinal 为正整数。
- 两类 membership 新增非空 curated_revision_id / curated_revision_sha256 /
  approval_record_id / approval_evidence_sha256，FK 分别 RESTRICT 到加入时 T09
  的 approved_revision_id / approval_record_id；保存 hash 复制被引用不可变记录值。
- CuratedItem 外键改为 RESTRICT 删除语义（已编组的批准资产不得因删除/退审悬空）。
- 加约束前只读审计：重复 membership/ordinal、跨项目、非 approved、Benchmark
  非 supported source、无 EvidenceLink，任何命中均触发停止条件，不猜测保留哪条。
- deferred constraint trigger 验证 membership 的 revision/approval record 与
  CuratedItem 同项目/同条目、两个保存 hash 与源记录一致。
- Dataset/Benchmark 增加 composition_revision / composition_sha256 /
  composition_canonicalization_version / finalized_* 字段；finalize 为一次性
  （CHECK 保证 finalized 字段齐全且等于当时 composition 值）；finalized 后
  父记录 composition 字段与 membership 均由 trigger 禁止原地修改。
- 存量容器按当前 membership 回填 composition revision/hash（composition-cjson-v1
  可跨端重算）；空集合也有确定 hash。
- downgrade 预检：存在 membership 或 finalized 容器则停止回滚（丢数据）。
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# domain.composition 是容器级 hash 可跨端重算的唯一事实源（任务卡 §4/§11 验收 13）。
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (_REPO_ROOT / "libs" / "domain",):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from domain.composition import (  # noqa: E402
    COMPOSITION_CJSON_VERSION,
    composition_sha256,
)

revision: str = "t10_dataset_composition"
down_revision: str | Sequence[str] | None = "t09_curated_evidence_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEMBERSHIP_TABLES = (
    ("dataset_items", "dataset_id", "datasets"),
    ("benchmark_cases", "benchmark_id", "benchmarks"),
)

#: membership 表 -> 容器外键列名（供 upgrade/downgrade 共用）。
_CONTAINER_COLS = {"dataset_items": "dataset_id", "benchmark_cases": "benchmark_id"}


def _count(conn, sql: str, **params) -> int:
    row = conn.execute(sa.text(sql), params).scalar()
    return int(row or 0)


def _precheck_memberships(conn) -> None:
    """加约束前只读审计：任何历史非法 membership 命中即触发停止条件。"""
    # 1. 重复 membership 与重复 ordinal。
    for tbl, container_col, _ in _MEMBERSHIP_TABLES:
        dup_item = _count(
            conn,
            f"SELECT count(*) FROM (SELECT {container_col}, curated_item_id FROM {tbl} "
            f"GROUP BY {container_col}, curated_item_id HAVING count(*) > 1) d",
        )
        if dup_item:
            raise RuntimeError(
                f"[t10] 预检失败：{tbl} 存在 {dup_item} 组重复 membership；"
                f"命中停止条件，禁止静默删除/合并（任务卡 §12）"
            )
        dup_ord = _count(
            conn,
            f"SELECT count(*) FROM (SELECT {container_col}, ordinal FROM {tbl} "
            f"GROUP BY {container_col}, ordinal HAVING count(*) > 1) d",
        )
        if dup_ord:
            raise RuntimeError(
                f"[t10] 预检失败：{tbl} 存在 {dup_ord} 组重复 ordinal；"
                f"命中停止条件，禁止静默重排（任务卡 §12）"
            )

    # 2. 跨项目关联（membership 的 CuratedItem 与容器项目不一致）。
    for tbl, container_col, container_tbl in _MEMBERSHIP_TABLES:
        cross = _count(
            conn,
            f"SELECT count(*) FROM {tbl} m "
            f"JOIN {container_tbl} c ON c.id = m.{container_col} "
            f"JOIN curated_items ci ON ci.id = m.curated_item_id "
            f"WHERE c.project_id <> ci.project_id",
        )
        if cross:
            raise RuntimeError(
                f"[t10] 预检失败：{tbl} 存在 {cross} 条跨项目 membership；命中停止条件"
            )

    # 3. 非 approved item（无法确定获批 revision/approval record）。
    for tbl, _, _ in _MEMBERSHIP_TABLES:
        non_approved = _count(
            conn,
            f"SELECT count(*) FROM {tbl} m JOIN curated_items ci ON ci.id = m.curated_item_id "
            f"WHERE ci.status != 'approved' OR ci.approved_revision_id IS NULL "
            f"OR ci.approval_record_id IS NULL",
        )
        if non_approved:
            raise RuntimeError(
                f"[t10] 预检失败：{tbl} 存在 {non_approved} 条非 approved / 缺审批指针的 "
                f"membership；无法确定获批 revision，命中停止条件"
            )

    # 4. Benchmark 非 supported source。
    unsupported = _count(
        conn,
        "SELECT count(*) FROM benchmark_cases m "
        "JOIN curated_items ci ON ci.id = m.curated_item_id "
        "LEFT JOIN candidates cand ON cand.id = ci.candidate_id "
        "WHERE cand.review_verdict IS DISTINCT FROM 'supported'",
    )
    if unsupported:
        raise RuntimeError(
            f"[t10] 预检失败：{unsupported} 条 benchmark membership 的 source Candidate "
            f"verdict 非 supported；命中停止条件"
        )

    # 5. 无 EvidenceLink 的 approved item。
    no_evidence = _count(
        conn,
        "SELECT count(*) FROM ("
        "SELECT m.id FROM dataset_items m "
        "JOIN curated_items ci ON ci.id = m.curated_item_id "
        "LEFT JOIN evidence_links el ON el.curated_item_id = ci.id "
        "WHERE ci.status = 'approved' AND el.id IS NULL "
        "UNION ALL "
        "SELECT m.id FROM benchmark_cases m "
        "JOIN curated_items ci ON ci.id = m.curated_item_id "
        "LEFT JOIN evidence_links el ON el.curated_item_id = ci.id "
        "WHERE ci.status = 'approved' AND el.id IS NULL) d",
    )
    if no_evidence:
        raise RuntimeError(
            f"[t10] 预检失败：{no_evidence} 条 membership 编组的 approved item 无 "
            f"EvidenceLink；命中停止条件"
        )

    # 6. 已审批指针自身不一致（revision/record 绑定与 hash 对不上）——诚实回填前提。
    for tbl, _, _ in _MEMBERSHIP_TABLES:
        inconsistent = _count(
            conn,
            f"SELECT count(*) FROM {tbl} m "
            f"JOIN curated_items ci ON ci.id = m.curated_item_id "
            f"JOIN curated_revisions r ON r.id = ci.approved_revision_id "
            f"JOIN review_records rr ON rr.id = ci.approval_record_id "
            f"WHERE r.curated_item_id != ci.id OR rr.entity_id != ci.id "
            f"OR rr.entity_revision_id != r.id OR rr.revision_content_sha256 != r.content_sha256 "
            f"OR rr.evidence_sha256 IS NULL OR rr.canonicalization_version IS NULL",
        )
        if inconsistent:
            raise RuntimeError(
                f"[t10] 预检失败：{tbl} 存在 {inconsistent} 条审批指针不一致的 membership；"
                f"无法确定保存 hash，命中停止条件"
            )


def _backfill_memberships(conn) -> int:
    """把加入时 T09 的 approved revision/approval record 与保存 hash 复制到 membership。

    预检已保证全部 membership 可精确回填；hash 复制被引用不可变记录的对应值。
    """
    total = 0
    for tbl, _, _ in _MEMBERSHIP_TABLES:
        result = conn.execute(
            sa.text(
                f"UPDATE {tbl} m SET "
                f"curated_revision_id = ci.approved_revision_id, "
                f"curated_revision_sha256 = r.content_sha256, "
                f"approval_record_id = ci.approval_record_id, "
                f"approval_evidence_sha256 = rr.evidence_sha256 "
                f"FROM curated_items ci "
                f"JOIN curated_revisions r ON r.id = ci.approved_revision_id "
                f"JOIN review_records rr ON rr.id = ci.approval_record_id "
                f"WHERE m.curated_item_id = ci.id"
            )
        )
        total += int(result.rowcount or 0)
        print(f"[t10] membership backfill: {tbl} 回填 {result.rowcount} 条")
    return total


def _backfill_composition(conn) -> int:
    """为存量容器回填 composition revision/hash（composition-cjson-v1 可跨端重算）。

    空容器也有确定 hash；revision 按当前 membership 数诚实回填（无法还原历史移除）。
    """
    containers_backfilled = 0
    for tbl, container_col, container_tbl, ctype in (
        ("dataset_items", "dataset_id", "datasets", "dataset"),
        ("benchmark_cases", "benchmark_id", "benchmarks", "benchmark"),
    ):
        rows = conn.execute(
            sa.text(
                f"SELECT {container_col} AS container_id, id, ordinal, curated_item_id, "
                f"curated_revision_id, curated_revision_sha256, approval_record_id, "
                f"approval_evidence_sha256 FROM {tbl} ORDER BY {container_col}, ordinal, id"
            )
        ).fetchall()
        by_container: dict = {}
        for r in rows:
            by_container.setdefault(r["container_id"], []).append(r)
        containers = conn.execute(sa.text(f"SELECT id FROM {container_tbl}")).fetchall()
        for c in containers:
            memberships = [
                {
                    "membership_id": r["id"],
                    "ordinal": int(r["ordinal"]),
                    "curated_item_id": r["curated_item_id"],
                    "curated_revision_id": r["curated_revision_id"],
                    "curated_revision_sha256": r["curated_revision_sha256"],
                    "approval_record_id": r["approval_record_id"],
                    "approval_evidence_sha256": r["approval_evidence_sha256"],
                }
                for r in by_container.get(c["id"], [])
            ]
            comp_hash = composition_sha256(
                container_id=c["id"], container_type=ctype, memberships=memberships
            )
            conn.execute(
                sa.text(
                    f"UPDATE {container_tbl} SET composition_revision = :r, "
                    f"composition_sha256 = :h, "
                    f"composition_canonicalization_version = :v WHERE id = :id"
                ),
                {
                    "r": len(memberships),
                    "h": comp_hash,
                    "v": COMPOSITION_CJSON_VERSION,
                    "id": c["id"],
                },
            )
            containers_backfilled += 1
    print(f"[t10] composition backfill: {containers_backfilled} 个容器")
    return containers_backfilled


def _add_composition_columns(container_tbl: str) -> None:
    op.add_column(container_tbl, sa.Column("composition_revision", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column(container_tbl, sa.Column("composition_sha256", sa.String(length=64), nullable=False, server_default=sa.text("repeat('0'::text, 64)")))
    op.add_column(container_tbl, sa.Column("composition_canonicalization_version", sa.String(length=50), nullable=False, server_default=sa.text("'composition-cjson-v1'")))


def _add_finalized_columns(container_tbl: str) -> None:
    op.add_column(container_tbl, sa.Column("finalized_revision", sa.Integer(), nullable=True))
    op.add_column(container_tbl, sa.Column("finalized_sha256", sa.String(length=64), nullable=True))
    op.add_column(container_tbl, sa.Column("finalized_canonicalization_version", sa.String(length=50), nullable=True))
    op.add_column(container_tbl, sa.Column("finalized_by", sa.UUID(), nullable=True))
    op.add_column(container_tbl, sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))


def _create_membership_consistency_triggers(conn) -> None:
    """deferred constraint trigger：membership 固定 revision/approval/hash 与 CuratedItem 一致。"""
    for tbl, container_col, container_tbl in _MEMBERSHIP_TABLES:
        fn = f"trg_{tbl}_consistency"
        conn.execute(
            sa.text(
                f"""
                CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
                DECLARE ok boolean;
                BEGIN
                    SELECT EXISTS (
                        SELECT 1 FROM curated_items ci
                        JOIN curated_revisions r ON r.id = NEW.curated_revision_id
                        JOIN review_records rr ON rr.id = NEW.approval_record_id
                        JOIN {container_tbl} c ON c.id = NEW.{container_col}
                        WHERE ci.id = NEW.curated_item_id
                          AND c.project_id = ci.project_id
                          AND ci.approved_revision_id = NEW.curated_revision_id
                          AND ci.approval_record_id = NEW.approval_record_id
                          AND r.curated_item_id = ci.id
                          AND r.content_sha256 = NEW.curated_revision_sha256
                          AND rr.entity_type = 'curated_item'
                          AND rr.entity_id = ci.id
                          AND rr.action = 'approve'
                          AND rr.entity_revision_id = NEW.curated_revision_id
                          AND rr.evidence_sha256 = NEW.approval_evidence_sha256
                          AND NEW.curated_revision_sha256 ~ '^[0-9a-f]{{64}}$'
                          AND NEW.approval_evidence_sha256 ~ '^[0-9a-f]{{64}}$'
                    ) INTO ok;
                    IF NOT ok THEN
                        RAISE EXCEPTION '{tbl} membership consistency check failed';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        conn.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS {fn} ON {tbl}"
            )
        )
        conn.execute(
            sa.text(
                f"CREATE CONSTRAINT TRIGGER {fn} "
                f"AFTER INSERT OR UPDATE OF curated_item_id, curated_revision_id, "
                f"curated_revision_sha256, approval_record_id, approval_evidence_sha256 "
                f"ON {tbl} DEFERRABLE INITIALLY DEFERRED "
                f"FOR EACH ROW EXECUTE FUNCTION {fn}()"
            )
        )


def _create_finalized_immutable_triggers(conn) -> None:
    """finalized 容器不可变：父记录 UPDATE 与 membership INSERT/UPDATE/DELETE 双重门禁。

    membership DELETE 门禁只在其容器仍存在且已 finalized 时生效；容器自身级联删除
    （项目/容器删除）时容器行已先行删除，SELECT 返回空，不阻塞级联。
    """
    for container_tbl in ("datasets", "benchmarks"):
        fn = f"trg_{container_tbl}_finalized_immutable"
        conn.execute(
            sa.text(
                f"""
                CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
                BEGIN
                    IF OLD.status = 'finalized' THEN
                        RAISE EXCEPTION 'finalized {container_tbl} are immutable';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        conn.execute(sa.text(f"DROP TRIGGER IF EXISTS {fn} ON {container_tbl}"))
        conn.execute(
            sa.text(
                f"CREATE TRIGGER {fn} BEFORE UPDATE ON {container_tbl} "
                f"FOR EACH ROW EXECUTE FUNCTION {fn}()"
            )
        )

    for tbl, container_col, container_tbl in _MEMBERSHIP_TABLES:
        fn = f"trg_{tbl}_finalized_guard"
        conn.execute(
            sa.text(
                f"""
                CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
                DECLARE st text;
                BEGIN
                    SELECT status INTO st FROM {container_tbl}
                     WHERE id = COALESCE(NEW.{container_col}, OLD.{container_col});
                    IF st = 'finalized' THEN
                        RAISE EXCEPTION 'cannot modify memberships of a finalized {container_tbl}';
                    END IF;
                    RETURN COALESCE(NEW, OLD);
                END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        conn.execute(sa.text(f"DROP TRIGGER IF EXISTS {fn} ON {tbl}"))
        conn.execute(
            sa.text(
                f"CREATE TRIGGER {fn} BEFORE INSERT OR UPDATE OR DELETE ON {tbl} "
                f"FOR EACH ROW EXECUTE FUNCTION {fn}()"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. 加约束前只读审计（停止条件）。
    # ------------------------------------------------------------------
    _precheck_memberships(bind)

    # ------------------------------------------------------------------
    # 2. membership 扩展列（先 nullable，回填后收紧）。
    # ------------------------------------------------------------------
    for tbl in ("dataset_items", "benchmark_cases"):
        op.add_column(tbl, sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        op.add_column(tbl, sa.Column("curated_revision_id", sa.UUID(), nullable=True))
        op.add_column(tbl, sa.Column("curated_revision_sha256", sa.String(length=64), nullable=True))
        op.add_column(tbl, sa.Column("approval_record_id", sa.UUID(), nullable=True))
        op.add_column(tbl, sa.Column("approval_evidence_sha256", sa.String(length=64), nullable=True))

    # ------------------------------------------------------------------
    # 3. 诚实回填（复制被引用不可变记录的对应值）。
    # ------------------------------------------------------------------
    _backfill_memberships(bind)

    for tbl in ("dataset_items", "benchmark_cases"):
        for col in (
            "curated_revision_id",
            "curated_revision_sha256",
            "approval_record_id",
            "approval_evidence_sha256",
        ):
            op.alter_column(tbl, col, nullable=False)

    # ------------------------------------------------------------------
    # 4. CuratedItem 外键改为 RESTRICT + 新增固定 revision/record FK（RESTRICT）。
    # ------------------------------------------------------------------
    for tbl in ("dataset_items", "benchmark_cases"):
        op.drop_constraint(f"{tbl}_curated_item_id_fkey", tbl, type_="foreignkey")
        op.create_foreign_key(
            f"{tbl}_curated_item_id_fkey", tbl, "curated_items",
            ["curated_item_id"], ["id"], ondelete="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{tbl}_curated_revision", tbl, "curated_revisions",
            ["curated_revision_id"], ["id"], ondelete="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{tbl}_approval_record", tbl, "review_records",
            ["approval_record_id"], ["id"], ondelete="RESTRICT",
        )

    # ------------------------------------------------------------------
    # 5. 唯一约束 / CHECK / 索引。
    # ------------------------------------------------------------------
    for tbl in ("dataset_items", "benchmark_cases"):
        cc = _CONTAINER_COLS[tbl]
        op.create_unique_constraint(f"uq_{tbl}_{cc.replace('_id', '')}_item", tbl, [cc, "curated_item_id"])
        op.create_unique_constraint(f"uq_{tbl}_{cc.replace('_id', '')}_ordinal", tbl, [cc, "ordinal"])
        op.create_check_constraint(f"ck_{tbl}_ordinal_positive", tbl, "ordinal >= 1")
        op.create_check_constraint(
            f"ck_{tbl}_revision_sha256_format", tbl,
            "curated_revision_sha256 ~ '^[0-9a-f]{64}$'",
        )
        op.create_check_constraint(
            f"ck_{tbl}_evidence_sha256_format", tbl,
            "approval_evidence_sha256 ~ '^[0-9a-f]{64}$'",
        )
        op.create_index(f"ix_{tbl}_{cc.replace('_id', '')}_ordinal", tbl, [cc, "ordinal"])

    # ------------------------------------------------------------------
    # 6. 容器 composition 字段 + 回填。
    # ------------------------------------------------------------------
    _add_composition_columns("datasets")
    _add_composition_columns("benchmarks")
    _backfill_composition(bind)

    # ------------------------------------------------------------------
    # 7. finalized 字段。
    # ------------------------------------------------------------------
    _add_finalized_columns("datasets")
    _add_finalized_columns("benchmarks")

    # ------------------------------------------------------------------
    # 8. CHECK 约束。
    # ------------------------------------------------------------------
    for container_tbl in ("datasets", "benchmarks"):
        op.create_check_constraint(
            f"ck_{container_tbl}_finalized_complete", container_tbl,
            "status != 'finalized' OR ("
            "finalized_revision IS NOT NULL AND finalized_sha256 IS NOT NULL "
            "AND finalized_canonicalization_version IS NOT NULL "
            "AND finalized_by IS NOT NULL AND finalized_at IS NOT NULL "
            "AND finalized_revision = composition_revision "
            "AND finalized_sha256 = composition_sha256)",
        )
        op.create_check_constraint(
            f"ck_{container_tbl}_draft_no_finalize", container_tbl,
            "status != 'draft' OR ("
            "finalized_revision IS NULL AND finalized_sha256 IS NULL "
            "AND finalized_canonicalization_version IS NULL "
            "AND finalized_by IS NULL AND finalized_at IS NULL)",
        )
        op.create_check_constraint(
            f"ck_{container_tbl}_composition_sha256_format", container_tbl,
            "composition_sha256 ~ '^[0-9a-f]{64}$'",
        )
        op.create_check_constraint(
            f"ck_{container_tbl}_finalized_sha256_format", container_tbl,
            "finalized_sha256 IS NULL OR finalized_sha256 ~ '^[0-9a-f]{64}$'",
        )
        op.create_check_constraint(
            f"ck_{container_tbl}_composition_version", container_tbl,
            f"composition_canonicalization_version = '{COMPOSITION_CJSON_VERSION}'",
        )
        op.create_check_constraint(
            f"ck_{container_tbl}_finalized_version", container_tbl,
            f"finalized_canonicalization_version IS NULL OR "
            f"finalized_canonicalization_version = '{COMPOSITION_CJSON_VERSION}'",
        )

    # ------------------------------------------------------------------
    # 9. deferred consistency + finalized 不可变 trigger。
    # ------------------------------------------------------------------
    _create_membership_consistency_triggers(bind)
    _create_finalized_immutable_triggers(bind)


def downgrade() -> None:
    bind = op.get_bind()
    # 回滚预检：存在 membership 或 finalized 容器则停止（drop 列会丢失固定批准绑定）。
    try:
        memberships = _count(
            bind, "SELECT count(*) FROM dataset_items"
        ) + _count(bind, "SELECT count(*) FROM benchmark_cases")
        finalized = _count(
            bind, "SELECT count(*) FROM datasets WHERE status = 'finalized'"
        ) + _count(bind, "SELECT count(*) FROM benchmarks WHERE status = 'finalized'")
    except sa.exc.ProgrammingError:
        memberships = finalized = 0
    if memberships or finalized:
        raise RuntimeError(
            f"[t10] 回滚预检失败：存在 membership（{memberships}）或 finalized "
            f"容器（{finalized}）；请先清理新格式编组数据后再回滚。"
        )

    # trigger 依赖这些列，先删。
    for tbl, _, _ in _MEMBERSHIP_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_consistency ON {tbl}")
        op.execute(f"DROP FUNCTION IF EXISTS trg_{tbl}_consistency()")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_finalized_guard ON {tbl}")
        op.execute(f"DROP FUNCTION IF EXISTS trg_{tbl}_finalized_guard()")
    for container_tbl in ("datasets", "benchmarks"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{container_tbl}_finalized_immutable ON {container_tbl}")
        op.execute(f"DROP FUNCTION IF EXISTS trg_{container_tbl}_finalized_immutable()")

    # CHECK 约束。
    for container_tbl in ("datasets", "benchmarks"):
        for ck in (
            f"ck_{container_tbl}_finalized_version",
            f"ck_{container_tbl}_composition_version",
            f"ck_{container_tbl}_finalized_sha256_format",
            f"ck_{container_tbl}_composition_sha256_format",
            f"ck_{container_tbl}_draft_no_finalize",
            f"ck_{container_tbl}_finalized_complete",
        ):
            op.drop_constraint(ck, container_tbl, type_="check")
    for tbl in ("dataset_items", "benchmark_cases"):
        for ck in (
            f"ck_{tbl}_evidence_sha256_format",
            f"ck_{tbl}_revision_sha256_format",
            f"ck_{tbl}_ordinal_positive",
        ):
            op.drop_constraint(ck, tbl, type_="check")

    # finalized / composition 列。
    for container_tbl in ("datasets", "benchmarks"):
        for col in ("finalized_at", "finalized_by", "finalized_canonicalization_version", "finalized_sha256", "finalized_revision"):
            op.drop_column(container_tbl, col)
        for col in ("composition_canonicalization_version", "composition_sha256", "composition_revision"):
            op.drop_column(container_tbl, col)

    # 索引与唯一约束。
    for tbl in ("dataset_items", "benchmark_cases"):
        cc = _CONTAINER_COLS[tbl]
        op.drop_index(f"ix_{tbl}_{cc.replace('_id', '')}_ordinal", table_name=tbl)
        op.drop_constraint(f"uq_{tbl}_{cc.replace('_id', '')}_ordinal", tbl, type_="unique")
        op.drop_constraint(f"uq_{tbl}_{cc.replace('_id', '')}_item", tbl, type_="unique")

    # FK：先删新增的固定 revision/record FK，再把 curated_item FK 恢复为 NO ACTION。
    for tbl in ("dataset_items", "benchmark_cases"):
        op.drop_constraint(f"fk_{tbl}_approval_record", tbl, type_="foreignkey")
        op.drop_constraint(f"fk_{tbl}_curated_revision", tbl, type_="foreignkey")
        op.drop_constraint(f"{tbl}_curated_item_id_fkey", tbl, type_="foreignkey")
        op.create_foreign_key(
            f"{tbl}_curated_item_id_fkey", tbl, "curated_items",
            ["curated_item_id"], ["id"],
        )

    # membership 扩展列。
    for tbl in ("dataset_items", "benchmark_cases"):
        for col in ("approval_evidence_sha256", "approval_record_id", "curated_revision_sha256", "curated_revision_id", "created_at"):
            op.drop_column(tbl, col)
