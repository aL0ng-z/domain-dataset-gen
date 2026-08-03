import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

review_record_action_enum = ENUM(
    "approve", "reject", "needs_revision", "agree", "disagree",
    name="review_record_action", create_type=True,
)


class ReviewRecord(Base):
    """统一审核记录。

    T09 扩展：approve 记录必须冻结被批准 revision 的 id/content hash 与证据快照
    hash。记录插入后由数据库 trigger 禁止 UPDATE/DELETE（批准事实不可变）。
    """

    __tablename__ = "review_records"
    __table_args__ = (
        Index("ix_review_records_entity", "entity_type", "entity_id"),
        # T09：CuratedItem 审批（approve）记录的绑定字段不可为空；其它实体类型
        # 的历史 approve 记录（如 cleaned_document_version）不受新字段约束。
        CheckConstraint(
            "entity_type != 'curated_item' OR action != 'approve' OR ("
            "entity_revision_id IS NOT NULL AND revision_content_sha256 IS NOT NULL "
            "AND evidence_snapshot IS NOT NULL AND evidence_sha256 IS NOT NULL "
            "AND canonicalization_version IS NOT NULL"
            ")",
            name="ck_review_records_curated_approve_binding",
        ),
        CheckConstraint(
            "entity_type != 'curated_item' OR action = 'approve' OR ("
            "entity_revision_id IS NULL AND revision_content_sha256 IS NULL "
            "AND evidence_snapshot IS NULL AND evidence_sha256 IS NULL "
            "AND canonicalization_version IS NULL"
            ")",
            name="ck_review_records_curated_non_approve_no_binding",
        ),
        CheckConstraint(
            "revision_content_sha256 IS NULL OR revision_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_review_records_sha256_format",
        ),
        CheckConstraint(
            "evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_review_records_evidence_sha256_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(review_record_action_enum, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # T09：审批绑定（仅 action=approve 非空）。
    entity_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curated_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    revision_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonicalization_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
