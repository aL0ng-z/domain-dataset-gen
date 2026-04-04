import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

cleaning_job_status_enum = ENUM("queued", "processing", "completed", "failed", name="cleaning_job_status", create_type=True)
section_status_enum = ENUM("draft", "in_cleaning", "review_pending", "accepted", "rejected", name="section_status", create_type=True)
comment_type_enum = ENUM("parse_issue", "ocr_issue", "layout_issue", "general", name="comment_type", create_type=True)


class CleaningJob(Base):
    __tablename__ = "cleaning_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    parse_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parse_jobs.id"))
    status: Mapped[str] = mapped_column(cleaning_job_status_enum, nullable=False, default="queued")
    started_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cleaning_job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cleaning_jobs.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_pages: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(section_status_enum, nullable=False, default="draft")
    cleaned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SectionLease(Base):
    __tablename__ = "section_leases"

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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"))
    revised_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    cleaned_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    revision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
