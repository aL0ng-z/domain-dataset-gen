import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

curated_item_status_enum = ENUM("draft", "approved", "exported", "deprecated", name="curated_item_status", create_type=True)

#: 版本化规范版本标识（任务卡 §4.2，与 domain.canonical 对齐）。
CURATED_CONTENT_CJSON_VERSION = "curated-content-cjson-v1"
CURATED_APPROVAL_CJSON_VERSION = "curated-approval-cjson-v1"


class CuratedItem(Base):
    __tablename__ = "curated_items"
    __table_args__ = (
        # T09：一个 Candidate 最多提升为一个 CuratedItem；并发重复提升只能成功一次。
        UniqueConstraint("candidate_id", name="uq_curated_items_candidate"),
        # T09：approved 当且仅当审批指针/reviewer/time 全部非空（单一状态源）。
        CheckConstraint(
            "status != 'approved' OR ("
            "approved_revision_id IS NOT NULL AND approval_record_id IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL"
            ")",
            name="ck_curated_items_approved_complete",
        ),
        CheckConstraint(
            "status != 'draft' OR ("
            "approved_revision_id IS NULL AND approval_record_id IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL"
            ")",
            name="ck_curated_items_draft_no_approval",
        ),
        CheckConstraint("current_revision >= 1", name="ck_curated_items_revision_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(curated_item_status_enum, nullable=False, default="draft")
    promoted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # T09：乐观修订 + 审批指针。
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curated_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    approval_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_records.id", ondelete="RESTRICT"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CuratedRevision(Base):
    __tablename__ = "curated_revisions"
    __table_args__ = (
        # T09：同一条目内 version 唯一；修订只能 INSERT vN+1，不能覆写。
        UniqueConstraint("curated_item_id", "version", name="uq_curated_revisions_item_version"),
        CheckConstraint("version >= 1", name="ck_curated_revisions_version_positive"),
        # content_sha256 为 64 位小写十六进制 SHA-256。
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_curated_revisions_sha256_format",
        ),
        Index("ix_curated_revisions_item_version", "curated_item_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id", ondelete="CASCADE"))
    revised_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonicalization_version: Mapped[str] = mapped_column(String(50), nullable=False)
    revision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        # T09：同一条目内证据坐标唯一（去重提升不会产生重复 EvidenceLink）。
        UniqueConstraint(
            "curated_item_id", "chunk_id", "start_char", "end_char",
            name="uq_evidence_links_item_chunk_offsets",
        ),
        CheckConstraint("start_char >= 0", name="ck_evidence_links_start_nonneg"),
        CheckConstraint("end_char > start_char", name="ck_evidence_links_end_gt_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chunks.id"))
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    source_pages: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
