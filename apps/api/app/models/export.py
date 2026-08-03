import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SnapshotManifest(Base):
    """T11：完整冻结数据快照（不只 ID 列表），插入后不可更新/删除。

    - ``manifest`` JSONB 保存完整 canonical manifest（成员、内容、证据、版本图）。
    - ``manifest_sha256`` 为 manifest-cjson-v1 规范字节的 SHA-256（与
      domain.manifest 一致，hash 排除自身字段）。
    - 现有单值 ``chunk_set_id/cleaned_version_id/generation_batch_id`` 仅保留为
      legacy 兼容，不再代表多文档快照；新快照的完整多值关系写入 manifest。
    """

    __tablename__ = "snapshot_manifests"
    __table_args__ = (
        # 完整封存快照：export_id 唯一（一对一到 Export），插入后禁止 UPDATE/DELETE。
        UniqueConstraint("export_id", name="uq_snapshot_manifests_export"),
        # 新格式（is_legacy=false）必须带 hash；legacy 旧快照不伪造完整性。
        CheckConstraint(
            "is_legacy = true OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_snapshot_manifests_sha256_format",
        ),
        CheckConstraint(
            "canonicalization_version IN ('manifest-cjson-v1', 'legacy-unverified')",
            name="ck_snapshot_manifests_canon_version",
        ),
        Index("ix_snapshot_manifests_export", "export_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    export_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exports.id", ondelete="RESTRICT", use_alter=True, name="fk_snapshot_manifests_export"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # T11：schema/canonicalization 版本 + hash + 封存时间。
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    canonicalization_version: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # T11：迁移前旧快照标记（无 canonical hash，不伪造完整性）。
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # legacy 兼容（仅历史行使用，新快照为 NULL）。
    chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
    cleaned_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaned_document_versions.id"), nullable=True)
    generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_batches.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Export(Base):
    """T11：导出记录。queued/processing/failed 可推进或重试；completed 后不可改。

    - ``status``：queued/processing/completed/failed；只有 completed 可下载。
    - completed 时全部产物字段（bucket/minio_key/object_version_id/content_type/
      output_sha256/file_size/manifest_key/manifest_object_version_id/
      manifest_sha256/artifact_seal_id/item_count）必须非空（CHECK）。
    - ``request_fingerprint``：请求摘要（expected revision/hash），用于一致性校验。
    - ``is_legacy``：迁移前旧记录为 true（不满足新格式完整字段合同）。
    """

    __tablename__ = "exports"
    __table_args__ = (
        # 唯一 source：dataset 与 benchmark 恰好一个。
        CheckConstraint(
            "num_nonnulls(dataset_id, benchmark_id) = 1",
            name="ck_exports_single_source",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_exports_status_valid",
        ),
        # completed 时产物字段必须齐全。
        CheckConstraint(
            "is_legacy = true OR status != 'completed' OR ("
            "bucket_name IS NOT NULL AND minio_key IS NOT NULL AND object_version_id IS NOT NULL "
            "AND content_type IS NOT NULL AND output_sha256 IS NOT NULL AND file_size IS NOT NULL "
            "AND manifest_key IS NOT NULL AND manifest_object_version_id IS NOT NULL "
            "AND manifest_sha256 IS NOT NULL AND artifact_seal_id IS NOT NULL "
            "AND item_count IS NOT NULL AND item_count >= 0"
            ")",
            name="ck_exports_completed_complete",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_exports_fingerprint_format",
        ),
        CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_exports_output_sha256_format",
        ),
        CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_exports_manifest_sha256_format",
        ),
        # T07：failed 需要 error_code/message。
        CheckConstraint(
            "is_legacy = true OR status != 'failed' OR (error_code IS NOT NULL AND error_message IS NOT NULL)",
            name="ck_exports_failed_has_error",
        ),
        # artifact_seal_id 唯一（一对一到 seal）+ 由 deferred constraint trigger 复核。
        UniqueConstraint("artifact_seal_id", name="uq_exports_artifact_seal"),
        # 新格式部分唯一索引：同 (bucket,key,version) 不允许重复（见迁移）。
        Index(
            "uq_exports_object_unique",
            "bucket_name", "minio_key", "object_version_id",
            unique=True,
            postgresql_where=text("is_legacy = false AND object_version_id IS NOT NULL"),
        ),
        Index("ix_exports_project_created", "project_id", text("created_at DESC")),
        Index("ix_exports_task_id", "task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"))
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=True)
    benchmark_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("benchmarks.id", ondelete="RESTRICT"), nullable=True)
    export_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("export_profiles.id", ondelete="RESTRICT"))
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # T11：请求快照指纹 + profile 快照 + formatter 版本。
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    formatter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # T11：任务关联（重试时指向当前 Task）。
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    format: Mapped[str] = mapped_column(String(50), nullable=False)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 产物对象（完成前可空）。
    bucket_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    minio_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    object_version_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    manifest_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manifest_object_version_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_seal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("export_artifact_seals.id", ondelete="RESTRICT", use_alter=True, name="fk_exports_artifact_seal"),
        nullable=True,
    )
    snapshot_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshot_manifests.id", ondelete="RESTRICT"), nullable=True
    )
    # T11：legacy 旧记录标记（不满足完整字段合同）。
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # 完成/失败时间。
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ExportArtifactSeal(Base):
    """T11：一对一封存表。只在两个对象上传完成后插入一次；不可更新/删除。

    - ``seal_payload`` 保存精确封存载荷（不含 seal_sha256 自身）。
    - ``seal_sha256`` 为 artifact-seal-cjson-v1 规范字节的 SHA-256，由
      BEFORE INSERT trigger 用共享 canonical helper 复算核对。
    - ``seal_version`` 必须为 ``artifact-seal-cjson-v1``。
    - 两个对象坐标（bucket/key/version）分别 UNIQUE 且不得相同。
    """

    __tablename__ = "export_artifact_seals"
    __table_args__ = (
        UniqueConstraint("seal_sha256", name="uq_export_artifact_seals_sha256"),
        UniqueConstraint("manifest_bucket", "manifest_key", "manifest_object_version_id", name="uq_export_artifact_seals_manifest_obj"),
        UniqueConstraint("output_bucket", "output_key", "output_object_version_id", name="uq_export_artifact_seals_output_obj"),
        CheckConstraint(
            "seal_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_artifact_seals_sha256_format",
        ),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_artifact_seals_manifest_hash_format",
        ),
        CheckConstraint(
            "output_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_export_artifact_seals_output_hash_format",
        ),
        CheckConstraint(
            "manifest_size >= 0 AND output_size >= 0",
            name="ck_export_artifact_seals_sizes_nonneg",
        ),
        CheckConstraint(
            "manifest_key IS NOT NULL AND manifest_object_version_id IS NOT NULL "
            "AND manifest_content_type IS NOT NULL",
            name="ck_export_artifact_seals_manifest_fields",
        ),
        CheckConstraint(
            "output_key IS NOT NULL AND output_object_version_id IS NOT NULL "
            "AND output_content_type IS NOT NULL",
            name="ck_export_artifact_seals_output_fields",
        ),
        CheckConstraint(
            "(manifest_bucket, manifest_key, manifest_object_version_id) <> "
            "(output_bucket, output_key, output_object_version_id)",
            name="ck_export_artifact_seals_distinct_objects",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    export_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exports.id", ondelete="RESTRICT"), nullable=False)
    snapshot_manifest_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("snapshot_manifests.id", ondelete="RESTRICT"), nullable=False)
    seal_version: Mapped[str] = mapped_column(String(50), nullable=False)
    seal_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seal_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ---- manifest 对象 ----
    manifest_bucket: Mapped[str] = mapped_column(String(200), nullable=False)
    manifest_key: Mapped[str] = mapped_column(String(500), nullable=False)
    manifest_object_version_id: Mapped[str] = mapped_column(String(200), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    manifest_content_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # ---- output 对象 ----
    output_bucket: Mapped[str] = mapped_column(String(200), nullable=False)
    output_key: Mapped[str] = mapped_column(String(500), nullable=False)
    output_object_version_id: Mapped[str] = mapped_column(String(200), nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_content_type: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
