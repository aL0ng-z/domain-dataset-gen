import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

cleaned_document_version_status_enum = ENUM(
    "draft", "review_pending", "accepted", "rejected",
    name="cleaned_document_version_status", create_type=True,
)


class CleanedDocumentVersion(Base):
    __tablename__ = "cleaned_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_cleaned_doc_ver_doc_version"),
        UniqueConstraint("document_id", "merge_idempotency_key", name="uq_cleaned_doc_ver_doc_idem"),
        UniqueConstraint("artifact_key", name="uq_cleaned_doc_ver_artifact_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    source_cleaning_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaning_jobs.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    merged_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_revision_map: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_revision_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merge_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(cleaned_document_version_status_enum, nullable=False, default="review_pending")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
