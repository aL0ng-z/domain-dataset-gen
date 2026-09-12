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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

cleaning_job_status_enum = ENUM("queued", "processing", "completed", "failed", "cancelled", name="cleaning_job_status", create_type=True)
section_status_enum = ENUM("draft", "in_cleaning", "review_pending", "accepted", "rejected", name="section_status", create_type=True)
comment_type_enum = ENUM("parse_issue", "ocr_issue", "layout_issue", "general", name="comment_type", create_type=True)
section_assignment_status_enum = ENUM(
    "unassigned", "assigned", "in_progress", "completed", "returned",
    name="section_assignment_status", create_type=True,
)


class CleaningJob(Base):
    __tablename__ = "cleaning_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    parse_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parse_jobs.id"))
    status: Mapped[str] = mapped_column(cleaning_job_status_enum, nullable=False, default="queued")
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (
        CheckConstraint("content_revision >= 0", name="ck_sections_content_revision_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cleaning_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaning_jobs.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 页面分段器写入页码数组；保留旧记录的 JSON object 兼容性。
    source_pages: Mapped[list[int] | dict | None] = mapped_column(JSONB, nullable=True)
    raw_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(section_status_enum, nullable=False, default="draft")
    cleaned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assignment_status: Mapped[str] = mapped_column(section_assignment_status_enum, nullable=False, server_default="unassigned")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lease: Mapped["SectionLease | None"] = relationship(
        "SectionLease",
        primaryjoin="and_(SectionLease.section_id == Section.id, SectionLease.released_at.is_(None), SectionLease.expires_at > func.now())",
        uselist=False,
        viewonly=True,
        lazy="selectin",
    )


class SectionLease(Base):
    __tablename__ = "section_leases"
    __table_args__ = (
        Index(
            "uq_section_leases_active_section",
            "section_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index("ix_section_leases_section_expires", "section_id", "expires_at"),
        CheckConstraint("expires_at > acquired_at", name="ck_section_leases_expiry_after_acquire"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SectionComment(Base):
    __tablename__ = "section_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    comment_type: Mapped[str] = mapped_column(comment_type_enum, nullable=False, default="general")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SectionRevision(Base):
    __tablename__ = "section_revisions"
    __table_args__ = (
        CheckConstraint("to_revision = from_revision + 1", name="ck_section_revisions_contiguous"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"))
    revised_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    cleaned_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    revision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
