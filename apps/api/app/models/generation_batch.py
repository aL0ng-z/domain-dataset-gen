import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# T08：generation_batch_status 增加 cancelled；新写状态机
# pending -> processing -> completed|failed|cancelled。旧值 review_pending 仅兼容历史读取。
generation_batch_status_enum = ENUM(
    "pending", "processing", "review_pending", "completed", "failed", "cancelled",
    name="generation_batch_status", create_type=True,
)

#: provenance_status 允许值（任务卡 §4）。
PROVENANCE_VERIFIED = "verified"
PROVENANCE_LEGACY_UNAVAILABLE = "legacy_unavailable"
PROVENANCE_INVALID = "invalid"


class GenerationBatch(Base):
    __tablename__ = "generation_batches"
    __table_args__ = (
        # 一条 batch 最多一个直接 retry 后继（线性派生链，不产生分叉）。
        UniqueConstraint(
            "retry_of_generation_batch_id",
            name="uq_generation_batches_single_retry_successor",
        ),
        # T08：计数与选择集一致（legacy 历史行无 selected_chunk_ids 则豁免）。
        CheckConstraint(
            "total_chunks >= 0 AND completed_chunks >= 0 "
            "AND completed_chunks <= total_chunks",
            name="ck_generation_batches_chunk_counts",
        ),
        CheckConstraint(
            "is_legacy = true OR ("
            "selected_chunk_ids IS NOT NULL "
            "AND jsonb_array_length(selected_chunk_ids) = total_chunks"
            ")",
            name="ck_generation_batches_total_matches_selected",
        ),
        # 非 legacy 新写批次必须带齐归属字段。
        CheckConstraint(
            "is_legacy = true OR ("
            "chunk_set_id IS NOT NULL AND model_config_id IS NOT NULL "
            "AND prompt_template_id IS NOT NULL AND selected_chunk_ids IS NOT NULL"
            ")",
            name="ck_generation_batches_required_not_legacy",
        ),
        # provenance 约束（任务卡 §4）：
        # - is_legacy=false -> provenance_status='verified'
        # - verified 时必须冻结全部快照/hash/version/renderer
        # - legacy_unavailable|invalid 必须 is_legacy=true 且带 error code
        CheckConstraint(
            "is_legacy = true OR provenance_status = 'verified'",
            name="ck_generation_batches_verified_not_legacy",
        ),
        CheckConstraint(
            "provenance_status != 'verified' OR ("
            "prompt_template_version_id IS NOT NULL "
            "AND prompt_template_snapshot IS NOT NULL "
            "AND prompt_template_sha256 IS NOT NULL "
            "AND model_config_snapshot IS NOT NULL "
            "AND model_config_sha256 IS NOT NULL "
            "AND renderer_version IS NOT NULL"
            ")",
            name="ck_generation_batches_verified_has_snapshot",
        ),
        CheckConstraint(
            "provenance_status NOT IN ('legacy_unavailable', 'invalid') OR "
            "(is_legacy = true AND provenance_error_code IS NOT NULL)",
            name="ck_generation_batches_legacy_unavailable_error",
        ),
        CheckConstraint(
            "provenance_status IN ('verified', 'legacy_unavailable', 'invalid')",
            name="ck_generation_batches_provenance_status_valid",
        ),
        # 终态必须设置 completed_at（legacy 迁移前行由迁移回填诚实标记，不强制）。
        CheckConstraint(
            "is_legacy = true OR ("
            "status IN ('completed', 'failed', 'cancelled') = (completed_at IS NOT NULL)"
            ")",
            name="ck_generation_batches_terminal_completed_at",
        ),
        Index(
            "ix_generation_batches_document_created",
            "document_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    # 迁移前 legacy 行允许缺 chunk_set/model_config/prompt_template/selected_chunk_ids；
    # 非 legacy 新写必填由 CHECK ck_generation_batches_required_not_legacy 强制。
    chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True)
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_templates.id"), nullable=True)
    selected_chunk_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(generation_batch_status_enum, nullable=False, default="pending")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # ---- T08：retry 线性派生链 ----
    retry_of_generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_batches.id", ondelete="RESTRICT"), nullable=True
    )

    # ---- T08：冻结快照 / hash / renderer ----
    prompt_template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_template_versions.id", ondelete="RESTRICT"), nullable=True
    )
    prompt_template_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    prompt_template_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_config_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    renderer_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ---- T08：legacy / provenance 审计 ----
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    provenance_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="verified")
    provenance_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
