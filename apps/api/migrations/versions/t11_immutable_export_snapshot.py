"""T11: 不可变导出与完整快照 - Export/SnapshotManifest/ArtifactSeal 扩展与封存 trigger

Revision ID: t11_immutable_export_snapshot
Revises: t10_dataset_composition
Create Date: 2026-08-03

合同要点（对齐 T11 §4/§5）：
- exports：status/request_fingerprint/profile_snapshot/formatter_version/task_id/
  retry_count/error_code/error_message/产物对象字段（bucket/minio_key/
  object_version_id/content_type/output_sha256/file_size/manifest_key/
  manifest_object_version_id/manifest_sha256/artifact_seal_id）/is_legacy。
  completed 时产物字段必须全部非空；source 唯一 CHECK；failed 必须带 error。
- snapshot_manifests：export_id/project_id/schema_version/canonicalization_version/
  manifest_sha256/sealed_at/is_legacy；插入后不可 UPDATE/DELETE。
- export_artifact_seals：一对一封存表；BEFORE INSERT trigger 用共享 canonical
  SQL helper 复算 hash、核对 payload/列与关联 Export/Snapshot；UPDATE/DELETE 拒绝。
- deferred constraint trigger：completed Export 恰有一个 seal，seal 与 Export 字段
  一致，artifact_seal_id/export_id/snapshot_manifest_id 互相匹配。
- 存量 Export/SnapshotManifest 全部标记 is_legacy=true（迁移不得联网读 MinIO、
  不得为旧记录补造 hash）；projects->exports 删除关系改为 RESTRICT。
- downgrade 预检：存在新格式（is_legacy=false）Export 或新快照则停止回滚。
- 建 trigger 前完成 DB 回填；之后快照不可再更新。
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# domain.manifest 是 manifest/seal hash 可跨端重算的唯一事实源（任务卡 §4.3/§4.4）。
_REPO_ROOT = Path(__file__).resolve().parents[4]
for _p in (_REPO_ROOT / "libs" / "domain",):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from domain.manifest import MANIFEST_CJSON_VERSION  # noqa: E402  # 供契约测试断言迁移复用

revision: str = "t11_immutable_export_snapshot"
down_revision: str | Sequence[str] | None = "t10_dataset_composition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEAL_VERSION = "artifact-seal-cjson-v1"
#: manifest canonicalization 版本标识（写入选定常量，供 CHECK 引用）。
MANIFEST_CANON_VERSION = "manifest-cjson-v1"


def _count(conn, sql: str, **params) -> int:
    row = conn.execute(sa.text(sql), params).scalar()
    return int(row or 0)


# ---------------------------------------------------------------------------
# 共享 canonical SQL helper（跨语言 golden fixture 验证过与 domain.manifest 一致）
# ---------------------------------------------------------------------------

_CANON_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION _t11_canon_jsonb(v jsonb) RETURNS text AS $$
DECLARE
    result text;
    elem jsonb;
    k text;
BEGIN
    IF v IS NULL THEN
        RETURN 'null';
    END IF;
    CASE jsonb_typeof(v)
        WHEN 'null' THEN RETURN 'null';
        WHEN 'boolean' THEN RETURN CASE WHEN v::text = 'true' THEN 'true' ELSE 'false' END;
        WHEN 'number' THEN RETURN v::text;
        WHEN 'string' THEN RETURN v::text;
        WHEN 'array' THEN
            result := '[';
            FOR elem IN SELECT * FROM jsonb_array_elements(v) LOOP
                IF result <> '[' THEN result := result || ','; END IF;
                result := result || _t11_canon_jsonb(elem);
            END LOOP;
            RETURN result || ']';
        WHEN 'object' THEN
            result := '{';
            FOR k, elem IN SELECT key, value FROM jsonb_each(v) ORDER BY key LOOP
                IF result <> '{' THEN result := result || ','; END IF;
                result := result || to_jsonb(k)::text || ':' || _t11_canon_jsonb(elem);
            END LOOP;
            RETURN result || '}';
        ELSE RETURN 'null';
    END CASE;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""


# ---------------------------------------------------------------------------
# immutable trigger
# ---------------------------------------------------------------------------


def _create_snapshot_immutable_trigger(conn) -> None:
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_snapshot_manifests_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'snapshot_manifests are immutable (insert-only)';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_snapshot_manifests_immutable ON snapshot_manifests")
    )
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_snapshot_manifests_immutable "
            "BEFORE UPDATE OR DELETE ON snapshot_manifests "
            "FOR EACH ROW EXECUTE FUNCTION trg_snapshot_manifests_immutable()"
        )
    )


def _create_seal_immutable_trigger(conn) -> None:
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_export_artifact_seals_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'export_artifact_seals are immutable (insert-only)';
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_export_artifact_seals_immutable ON export_artifact_seals")
    )
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_export_artifact_seals_immutable "
            "BEFORE UPDATE OR DELETE ON export_artifact_seals "
            "FOR EACH ROW EXECUTE FUNCTION trg_export_artifact_seals_immutable()"
        )
    )


def _create_seal_insert_trigger(conn) -> None:
    """BEFORE INSERT trigger：复算 hash、核对 payload/列与关联 Export/Snapshot。

    语义（任务卡 §4.3）：
    - seal_version 必须为 artifact-seal-cjson-v1；
    - seal_sha256 = SHA256(canonical_bytes(seal_payload 去掉 seal_sha256))；
    - seal_payload 必须逐项包含 export_id/snapshot_manifest_id、formatter/schema
      版本、manifest/output 两组对象字段，且与列值相等；
    - 关联的 SnapshotManifest 与 Export 必须存在且同一 export。
    """
    conn.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION trg_export_artifact_seals_insert() RETURNS trigger AS $$
            DECLARE
                payload_clean jsonb;
                payload_keys text[];
                expected_keys text[];
                canon_bytes bytea;
                computed_hash text;
                manifest_ok boolean;
                export_ok boolean;
                same_export boolean;
            BEGIN
                IF NEW.seal_version <> '{SEAL_VERSION}' THEN
                    RAISE EXCEPTION 'unexpected seal_version: %', NEW.seal_version;
                END IF;
                -- 去掉 seal_sha256 自身后计算 canonical 字节与 hash。
                payload_clean := NEW.seal_payload - 'seal_sha256';
                canon_bytes := convert_to(_t11_canon_jsonb(payload_clean), 'UTF8');
                computed_hash := encode(sha256(canon_bytes), 'hex');
                IF computed_hash <> NEW.seal_sha256 THEN
                    RAISE EXCEPTION 'seal_sha256 mismatch: computed % expected %', computed_hash, NEW.seal_sha256;
                END IF;
                -- seal_payload 必须包含 export_id/snapshot_manifest_id/formatter/schema 版本。
                payload_keys := ARRAY(SELECT jsonb_object_keys(NEW.seal_payload) ORDER BY 1);
                expected_keys := ARRAY[
                    'export_id', 'snapshot_manifest_id', 'seal_version', 'schema_version',
                    'formatter_version', 'manifest', 'output'
                ];
                IF NOT (payload_keys @> expected_keys) THEN
                    RAISE EXCEPTION 'seal_payload missing required keys';
                END IF;
                -- payload 中的 export/snapshot id 必须与列一致。
                IF NEW.seal_payload->>'export_id' IS DISTINCT FROM NEW.export_id::text
                   OR NEW.seal_payload->>'snapshot_manifest_id' IS DISTINCT FROM NEW.snapshot_manifest_id::text THEN
                    RAISE EXCEPTION 'seal_payload id mismatch';
                END IF;
                -- payload 中的对象字段必须与列一致（manifest/output 两组）。
                IF NOT (
                    NEW.seal_payload->'manifest'->>'bucket' IS NOT DISTINCT FROM NEW.manifest_bucket
                    AND NEW.seal_payload->'manifest'->>'key' IS NOT DISTINCT FROM NEW.manifest_key
                    AND NEW.seal_payload->'manifest'->>'object_version_id' IS NOT DISTINCT FROM NEW.manifest_object_version_id
                    AND NEW.seal_payload->'manifest'->>'sha256' IS NOT DISTINCT FROM NEW.manifest_sha256
                    AND (NEW.seal_payload->'manifest'->>'size')::bigint IS NOT DISTINCT FROM NEW.manifest_size
                    AND NEW.seal_payload->'manifest'->>'content_type' IS NOT DISTINCT FROM NEW.manifest_content_type
                    AND NEW.seal_payload->'output'->>'bucket' IS NOT DISTINCT FROM NEW.output_bucket
                    AND NEW.seal_payload->'output'->>'key' IS NOT DISTINCT FROM NEW.output_key
                    AND NEW.seal_payload->'output'->>'object_version_id' IS NOT DISTINCT FROM NEW.output_object_version_id
                    AND NEW.seal_payload->'output'->>'sha256' IS NOT DISTINCT FROM NEW.output_sha256
                    AND (NEW.seal_payload->'output'->>'size')::bigint IS NOT DISTINCT FROM NEW.output_size
                    AND NEW.seal_payload->'output'->>'content_type' IS NOT DISTINCT FROM NEW.output_content_type
                ) THEN
                    RAISE EXCEPTION 'seal_payload object fields mismatch';
                END IF;
                -- 关联 Export 与 Snapshot 必须存在且同一 export。
                SELECT EXISTS (
                    SELECT 1 FROM snapshot_manifests sm
                     WHERE sm.id = NEW.snapshot_manifest_id AND sm.export_id = NEW.export_id
                ) INTO manifest_ok;
                IF NOT manifest_ok THEN
                    RAISE EXCEPTION 'snapshot_manifest does not belong to this export';
                END IF;
                SELECT EXISTS (
                    SELECT 1 FROM exports e WHERE e.id = NEW.export_id
                ) INTO export_ok;
                IF NOT export_ok THEN
                    RAISE EXCEPTION 'export does not exist';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_export_artifact_seals_insert ON export_artifact_seals")
    )
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_export_artifact_seals_insert "
            "BEFORE INSERT ON export_artifact_seals "
            "FOR EACH ROW EXECUTE FUNCTION trg_export_artifact_seals_insert()"
        )
    )


def _create_completed_export_consistency_trigger(conn) -> None:
    """deferred constraint trigger：completed Export 与 seal 一对一且字段一致。

    语义（任务卡 §4.3）：completed Export 必须恰有一个 seal；
    artifact_seal_id/export_id/snapshot_manifest_id 互相匹配；Export 的
    hash/key/version/size/content type 与 seal 完全一致。
    """
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_exports_completed_seal() RETURNS trigger AS $$
            DECLARE
                seal_ok boolean;
                n_seals integer;
            BEGIN
                IF NEW.status = 'completed' AND NOT NEW.is_legacy THEN
                    SELECT count(*) INTO n_seals
                      FROM export_artifact_seals s WHERE s.export_id = NEW.id;
                    IF n_seals <> 1 THEN
                        RAISE EXCEPTION 'completed export must have exactly one artifact seal (got %)', n_seals;
                    END IF;
                    IF NEW.artifact_seal_id IS NULL THEN
                        RAISE EXCEPTION 'completed export requires artifact_seal_id';
                    END IF;
                    SELECT EXISTS (
                        SELECT 1 FROM export_artifact_seals s
                         WHERE s.export_id = NEW.id AND s.id = NEW.artifact_seal_id
                           AND s.snapshot_manifest_id = NEW.snapshot_manifest_id
                           AND s.manifest_bucket = NEW.bucket_name
                           AND s.manifest_key = NEW.manifest_key
                           AND s.manifest_object_version_id = NEW.manifest_object_version_id
                           AND s.manifest_sha256 = NEW.manifest_sha256
                           AND s.output_bucket = NEW.bucket_name
                           AND s.output_key = NEW.minio_key
                           AND s.output_object_version_id = NEW.object_version_id
                           AND s.output_sha256 = NEW.output_sha256
                           AND s.output_size = NEW.file_size
                           AND s.output_content_type = NEW.content_type
                    ) INTO seal_ok;
                    IF NOT seal_ok THEN
                        RAISE EXCEPTION 'completed export artifact fields do not match its seal';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_exports_completed_seal ON exports")
    )
    conn.execute(
        sa.text(
            "CREATE CONSTRAINT TRIGGER trg_exports_completed_seal "
            "AFTER INSERT OR UPDATE OF status, artifact_seal_id, snapshot_manifest_id, "
            "bucket_name, minio_key, object_version_id, content_type, output_sha256, "
            "file_size, manifest_key, manifest_object_version_id, manifest_sha256, is_legacy "
            "ON exports DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION trg_exports_completed_seal()"
        )
    )


def _create_exports_immutable_trigger(conn) -> None:
    """completed Export 不可变：UPDATE/DELETE 均拒绝（除 status/error 的 T07 fenced 推进）。"""
    conn.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION trg_exports_completed_immutable() RETURNS trigger AS $$
            BEGIN
                IF OLD.status = 'completed' AND OLD.is_legacy = false THEN
                    RAISE EXCEPTION 'completed exports are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_exports_completed_immutable ON exports")
    )
    conn.execute(
        sa.text(
            "CREATE TRIGGER trg_exports_completed_immutable "
            "BEFORE UPDATE OR DELETE ON exports "
            "FOR EACH ROW EXECUTE FUNCTION trg_exports_completed_immutable()"
        )
    )


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. 回填前的 legacy 标记：存量 Export/SnapshotManifest 全部标 is_legacy=true。
    #    迁移不得联网读 MinIO，也不为旧记录补造 hash（任务卡 §6）。
    # ------------------------------------------------------------------
    # 先加 is_legacy 列（默认 false），回填后改默认并建约束。
    op.add_column("exports", sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column(
        "snapshot_manifests",
        sa.Column("is_legacy", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    legacy_exports = bind.execute(sa.text("UPDATE exports SET is_legacy = true")).rowcount
    legacy_snaps = bind.execute(sa.text("UPDATE snapshot_manifests SET is_legacy = true")).rowcount
    print(f"[t11] legacy marking: exports={legacy_exports} snapshot_manifests={legacy_snaps}")

    # ------------------------------------------------------------------
    # 2. snapshot_manifests 扩展列（legacy 行填充占位值，新行由服务层写入）。
    # ------------------------------------------------------------------
    op.add_column("snapshot_manifests", sa.Column("export_id", sa.UUID(), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("project_id", sa.UUID(), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("schema_version", sa.Integer(), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("canonicalization_version", sa.String(length=100), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("manifest_sha256", sa.String(length=64), nullable=True))
    op.add_column("snapshot_manifests", sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True))

    # legacy 回填：project_id 取关联 Export 的项目，其余占位（不伪造 hash）。
    bind.execute(
        sa.text(
            """
            UPDATE snapshot_manifests sm
               SET export_id = e.id, project_id = e.project_id, schema_version = 1,
                   canonicalization_version = 'legacy-unverified',
                   manifest_sha256 = repeat('0'::text, 64), sealed_at = sm.created_at
              FROM exports e
             WHERE e.snapshot_manifest_id = sm.id
            """
        )
    )

    # ------------------------------------------------------------------
    # 3. exports 扩展列（legacy 行填充占位，新行由服务层写入）。
    # ------------------------------------------------------------------
    op.add_column("exports", sa.Column("source_type", sa.String(length=20), nullable=True))
    op.add_column("exports", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("exports", sa.Column("profile_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("exports", sa.Column("formatter_version", sa.String(length=100), nullable=True))
    op.add_column("exports", sa.Column("task_id", sa.UUID(), nullable=True))
    op.add_column("exports", sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("exports", sa.Column("error_code", sa.String(length=80), nullable=True))
    op.add_column("exports", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("exports", sa.Column("status", sa.String(length=20), nullable=True))
    op.add_column("exports", sa.Column("bucket_name", sa.String(length=200), nullable=True))
    op.add_column("exports", sa.Column("object_version_id", sa.String(length=200), nullable=True))
    op.add_column("exports", sa.Column("content_type", sa.String(length=100), nullable=True))
    op.add_column("exports", sa.Column("output_sha256", sa.String(length=64), nullable=True))
    op.add_column("exports", sa.Column("file_size", sa.BigInteger(), nullable=True))
    op.add_column("exports", sa.Column("manifest_key", sa.String(length=500), nullable=True))
    op.add_column("exports", sa.Column("manifest_object_version_id", sa.String(length=200), nullable=True))
    op.add_column("exports", sa.Column("manifest_sha256", sa.String(length=64), nullable=True))
    op.add_column("exports", sa.Column("artifact_seal_id", sa.UUID(), nullable=True))
    op.add_column("exports", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("exports", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    # legacy 回填：status=completed、formatter/profile/fingerprint 占位（不伪造 hash）。
    bind.execute(
        sa.text(
            """
            UPDATE exports
               SET source_type = CASE WHEN dataset_id IS NOT NULL THEN 'dataset'
                                      WHEN benchmark_id IS NOT NULL THEN 'benchmark' END,
                   request_fingerprint = repeat('0'::text, 64),
                   profile_snapshot = '{}'::jsonb,
                   formatter_version = 'legacy-unverified',
                   status = 'completed',
                   completed_at = created_at,
                   updated_at = created_at
             WHERE is_legacy = true
            """
        )
    )

    # 新记录默认 status=queued。
    op.execute("ALTER TABLE exports ALTER COLUMN status SET DEFAULT 'queued'")
    op.execute("ALTER TABLE exports ALTER COLUMN minio_key DROP NOT NULL")
    op.execute("ALTER TABLE exports ALTER COLUMN item_count DROP NOT NULL")
    op.execute("ALTER TABLE exports ALTER COLUMN snapshot_manifest_id DROP NOT NULL")

    # ------------------------------------------------------------------
    # 4. export_artifact_seals 表。
    # ------------------------------------------------------------------
    op.create_table(
        "export_artifact_seals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("export_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_manifest_id", sa.UUID(), nullable=False),
        sa.Column("seal_version", sa.String(length=50), nullable=False),
        sa.Column("seal_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seal_sha256", sa.String(length=64), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("manifest_bucket", sa.String(length=200), nullable=False),
        sa.Column("manifest_key", sa.String(length=500), nullable=False),
        sa.Column("manifest_object_version_id", sa.String(length=200), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_size", sa.BigInteger(), nullable=False),
        sa.Column("manifest_content_type", sa.String(length=100), nullable=False),
        sa.Column("output_bucket", sa.String(length=200), nullable=False),
        sa.Column("output_key", sa.String(length=500), nullable=False),
        sa.Column("output_object_version_id", sa.String(length=200), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_size", sa.BigInteger(), nullable=False),
        sa.Column("output_content_type", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["export_id"], ["exports.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_manifest_id"], ["snapshot_manifests.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seal_sha256", name="uq_export_artifact_seals_sha256"),
        sa.UniqueConstraint(
            "manifest_bucket", "manifest_key", "manifest_object_version_id",
            name="uq_export_artifact_seals_manifest_obj",
        ),
        sa.UniqueConstraint(
            "output_bucket", "output_key", "output_object_version_id",
            name="uq_export_artifact_seals_output_obj",
        ),
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_sha256_format", "export_artifact_seals",
        "seal_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_manifest_hash_format", "export_artifact_seals",
        "manifest_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_output_hash_format", "export_artifact_seals",
        "output_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_sizes_nonneg", "export_artifact_seals",
        "manifest_size >= 0 AND output_size >= 0",
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_manifest_fields", "export_artifact_seals",
        "manifest_key IS NOT NULL AND manifest_object_version_id IS NOT NULL "
        "AND manifest_content_type IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_output_fields", "export_artifact_seals",
        "output_key IS NOT NULL AND output_object_version_id IS NOT NULL "
        "AND output_content_type IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_export_artifact_seals_distinct_objects", "export_artifact_seals",
        "(manifest_bucket, manifest_key, manifest_object_version_id) <> "
        "(output_bucket, output_key, output_object_version_id)",
    )

    # ------------------------------------------------------------------
    # 5. exports 新 FK / 唯一约束 / CHECK / 索引。
    # ------------------------------------------------------------------
    # source_type 派生自 dataset/benchmark，保留原 FK。
    op.create_foreign_key("fk_exports_task_id", "exports", "tasks", ["task_id"], ["id"], ondelete="RESTRICT")
    # 新格式 source_type 必须合法。
    op.create_check_constraint(
        "ck_exports_status_valid", "exports",
        "status IN ('queued', 'processing', 'completed', 'failed')",
    )
    op.create_check_constraint(
        "ck_exports_single_source", "exports",
        "is_legacy = true OR num_nonnulls(dataset_id, benchmark_id) = 1",
    )
    op.create_check_constraint(
        "ck_exports_fingerprint_format", "exports",
        "is_legacy = true OR request_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_exports_output_sha256_format", "exports",
        "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_exports_manifest_sha256_format", "exports",
        "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_exports_completed_complete", "exports",
        "is_legacy = true OR status != 'completed' OR ("
        "bucket_name IS NOT NULL AND minio_key IS NOT NULL AND object_version_id IS NOT NULL "
        "AND content_type IS NOT NULL AND output_sha256 IS NOT NULL AND file_size IS NOT NULL "
        "AND manifest_key IS NOT NULL AND manifest_object_version_id IS NOT NULL "
        "AND manifest_sha256 IS NOT NULL AND artifact_seal_id IS NOT NULL "
        "AND item_count IS NOT NULL AND item_count >= 0"
        ")",
    )
    op.create_check_constraint(
        "ck_exports_failed_has_error", "exports",
        "is_legacy = true OR status != 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
    )
    op.create_unique_constraint("uq_exports_artifact_seal", "exports", ["artifact_seal_id"])
    op.create_unique_constraint("uq_snapshot_manifests_export", "snapshot_manifests", ["export_id"])
    op.create_index("ix_exports_project_created", "exports", ["project_id", sa.text("created_at DESC")])
    op.create_index("ix_exports_task_id", "exports", ["task_id"])
    op.create_index("ix_snapshot_manifests_export", "snapshot_manifests", ["export_id"])
    # 新格式部分唯一索引：(bucket,key,version)。
    op.create_index(
        "uq_exports_object_unique", "exports", ["bucket_name", "minio_key", "object_version_id"],
        unique=True,
        postgresql_where=sa.text("is_legacy = false AND object_version_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_snapshot_manifests_sha256_format", "snapshot_manifests",
        "is_legacy = true OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_snapshot_manifests_canon_version", "snapshot_manifests",
        f"canonicalization_version IN ('{MANIFEST_CJSON_VERSION}', 'legacy-unverified')",
    )

    # 旧 snapshot_manifest_id 改为 RESTRICT（项目删除不得级联抹掉审计历史）。
    op.drop_constraint("exports_snapshot_manifest_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key(
        "exports_snapshot_manifest_id_fkey", "exports", "snapshot_manifests",
        ["snapshot_manifest_id"], ["id"], ondelete="RESTRICT",
    )
    # snapshot_manifests.export_id FK（延迟到表/列就绪后，用 use_alter 语义手动建）。
    op.create_foreign_key(
        "fk_snapshot_manifests_export", "snapshot_manifests", "exports",
        ["export_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_snapshot_manifests_project", "snapshot_manifests", "projects",
        ["project_id"], ["id"], ondelete="RESTRICT",
    )
    # exports.project_id 删除关系改为 RESTRICT（项目删除不得级联抹掉导出审计）。
    op.drop_constraint("exports_project_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key(
        "exports_project_id_fkey", "exports", "projects",
        ["project_id"], ["id"], ondelete="RESTRICT",
    )
    # export_profile_id / dataset_id / benchmark_id 删除关系改为 RESTRICT。
    op.drop_constraint("exports_export_profile_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key(
        "exports_export_profile_id_fkey", "exports", "export_profiles",
        ["export_profile_id"], ["id"], ondelete="RESTRICT",
    )
    op.drop_constraint("exports_dataset_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key(
        "exports_dataset_id_fkey", "exports", "datasets",
        ["dataset_id"], ["id"], ondelete="RESTRICT",
    )
    op.drop_constraint("exports_benchmark_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key(
        "exports_benchmark_id_fkey", "exports", "benchmarks",
        ["benchmark_id"], ["id"], ondelete="RESTRICT",
    )

    # 新格式必填字段。
    op.alter_column("exports", "request_fingerprint", nullable=False)
    op.alter_column("exports", "profile_snapshot", nullable=False)
    op.alter_column("exports", "formatter_version", nullable=False)
    op.alter_column("exports", "source_type", nullable=False)
    op.alter_column("snapshot_manifests", "export_id", nullable=False)
    op.alter_column("snapshot_manifests", "project_id", nullable=False)
    op.alter_column("snapshot_manifests", "schema_version", nullable=False)
    op.alter_column("snapshot_manifests", "canonicalization_version", nullable=False)
    op.alter_column("snapshot_manifests", "manifest_sha256", nullable=False)
    op.alter_column("snapshot_manifests", "sealed_at", nullable=False)

    # ------------------------------------------------------------------
    # 6. 共享 canonical helper + immutable/seal trigger。
    # ------------------------------------------------------------------
    op.execute(sa.text(_CANON_FUNCTION_SQL))
    _create_snapshot_immutable_trigger(bind)
    _create_exports_immutable_trigger(bind)
    _create_seal_immutable_trigger(bind)
    _create_seal_insert_trigger(bind)
    _create_completed_export_consistency_trigger(bind)


def downgrade() -> None:
    bind = op.get_bind()
    # 回滚预检：存在新格式（is_legacy=false）Export/Snapshot 则停止回滚。
    try:
        new_exports = _count(bind, "SELECT count(*) FROM exports WHERE is_legacy = false")
        new_snaps = _count(bind, "SELECT count(*) FROM snapshot_manifests WHERE is_legacy = false")
    except sa.exc.ProgrammingError:
        new_exports = new_snaps = 0
    if new_exports or new_snaps:
        raise RuntimeError(
            f"[t11] 回滚预检失败：存在新格式 Export（{new_exports}）/Snapshot "
            f"（{new_snaps}）；请先停止 export runner 并确认无 queued/processing "
            f"新格式 Export 后再回滚。"
        )

    # 先删 trigger（依赖列/表）。
    for trg, fn, tbl in (
        ("trg_exports_completed_seal", "trg_exports_completed_seal", "exports"),
        ("trg_exports_completed_immutable", "trg_exports_completed_immutable", "exports"),
        ("trg_export_artifact_seals_insert", "trg_export_artifact_seals_insert", "export_artifact_seals"),
        ("trg_export_artifact_seals_immutable", "trg_export_artifact_seals_immutable", "export_artifact_seals"),
        ("trg_snapshot_manifests_immutable", "trg_snapshot_manifests_immutable", "snapshot_manifests"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {tbl}")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.execute("DROP FUNCTION IF EXISTS _t11_canon_jsonb(jsonb)")

    # 删除 exports 新 FK/唯一/CHECK/索引。
    op.drop_index("uq_exports_object_unique", table_name="exports")
    op.drop_index("ix_snapshot_manifests_export", table_name="snapshot_manifests")
    op.drop_index("ix_exports_task_id", table_name="exports")
    op.drop_index("ix_exports_project_created", table_name="exports")
    op.drop_constraint("uq_snapshot_manifests_export", "snapshot_manifests", type_="unique")
    op.drop_constraint("uq_exports_artifact_seal", "exports", type_="unique")
    for ck in (
        "ck_snapshot_manifests_sha256_format",
        "ck_snapshot_manifests_canon_version",
    ):
        op.drop_constraint(ck, "snapshot_manifests", type_="check")
    for ck in (
        "ck_exports_completed_complete",
        "ck_exports_failed_has_error",
        "ck_exports_manifest_sha256_format",
        "ck_exports_output_sha256_format",
        "ck_exports_fingerprint_format",
        "ck_exports_single_source",
        "ck_exports_status_valid",
    ):
        op.drop_constraint(ck, "exports", type_="check")

    # 恢复 exports 原 FK（CASCADE/默认）。
    op.drop_constraint("fk_snapshot_manifests_project", "snapshot_manifests", type_="foreignkey")
    op.drop_constraint("fk_snapshot_manifests_export", "snapshot_manifests", type_="foreignkey")
    op.drop_constraint("exports_project_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key("exports_project_id_fkey", "exports", "projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint("exports_export_profile_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key("exports_export_profile_id_fkey", "exports", "export_profiles", ["export_profile_id"], ["id"])
    op.drop_constraint("exports_dataset_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key("exports_dataset_id_fkey", "exports", "datasets", ["dataset_id"], ["id"])
    op.drop_constraint("exports_benchmark_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key("exports_benchmark_id_fkey", "exports", "benchmarks", ["benchmark_id"], ["id"])
    op.drop_constraint("exports_snapshot_manifest_id_fkey", "exports", type_="foreignkey")
    op.create_foreign_key("exports_snapshot_manifest_id_fkey", "exports", "snapshot_manifests", ["snapshot_manifest_id"], ["id"])
    op.drop_constraint("fk_exports_task_id", "exports", type_="foreignkey")

    # 删除 export_artifact_seals 表。
    op.drop_table("export_artifact_seals")

    # 删除 exports 扩展列。
    for col in (
        "updated_at", "completed_at", "artifact_seal_id", "manifest_sha256",
        "manifest_object_version_id", "manifest_key", "file_size", "output_sha256",
        "content_type", "object_version_id", "bucket_name", "status", "error_message",
        "error_code", "retry_count", "task_id", "formatter_version", "profile_snapshot",
        "request_fingerprint", "source_type", "is_legacy",
    ):
        op.drop_column("exports", col)
    op.alter_column("exports", "minio_key", nullable=False)
    op.alter_column("exports", "item_count", nullable=False)
    op.alter_column("exports", "snapshot_manifest_id", nullable=False)

    # 删除 snapshot_manifests 扩展列。
    for col in ("is_legacy", "sealed_at", "manifest_sha256", "canonicalization_version", "schema_version", "project_id", "export_id"):
        op.drop_column("snapshot_manifests", col)
