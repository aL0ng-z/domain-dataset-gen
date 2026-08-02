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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# T06：chunk_set_status 增加 failed/cancelled（与迁移 t06_versioned_chunking 一致）。
chunk_set_status_enum = ENUM(
    "pending", "processing", "review_pending", "completed", "rejected", "failed", "cancelled",
    name="chunk_set_status", create_type=True,
)


class ChunkSet(Base):
    """一次切分的不可覆盖版本：绑定已接受的 CleanedDocumentVersion 与冻结配置。

    T06 §4.1 合同：
    - version 在 Document 内唯一；历史集合不可覆盖。
    - is_legacy=true 的行允许缺 cleaned version/profile/config/task（迁移前数据）。
    - idempotency_key 非空时 (document_id, idempotency_key) 唯一。
    - source/output sha256、splitter_version 冻结；completed_at 记录完成时刻。
    - task_id 表示首次派发 Task，对所有 is_legacy=false 记录必填。
    - config_json 必须冻结 strategy/max_tokens/overlap_tokens/options/
      tokenizer_name/tokenizer_version/splitter_version。
    """

    __tablename__ = "chunk_sets"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_chunk_sets_doc_version"),
        UniqueConstraint("document_id", "idempotency_key", name="uq_chunk_sets_doc_idem"),
        UniqueConstraint("task_id", name="uq_chunk_sets_task_id"),
        # 同一 Document 最多一个 pending/processing 集合（并发完成时避免“最后写入者获胜”）。
        Index(
            "uq_chunk_sets_single_active",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
        CheckConstraint(
            "is_legacy = true OR ("
            "cleaned_document_version_id IS NOT NULL AND chunk_profile_id IS NOT NULL "
            "AND strategy IS NOT NULL AND config_json IS NOT NULL AND task_id IS NOT NULL"
            ")",
            name="ck_chunk_sets_legacy_required_fields",
        ),
        CheckConstraint("version > 0", name="ck_chunk_sets_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    cleaned_document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaned_document_versions.id"), nullable=True)
    chunk_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_profiles.id"), nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(chunk_set_status_enum, nullable=False, default="pending")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    artifact_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---- T06 版本化字段 ----
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    splitter_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
