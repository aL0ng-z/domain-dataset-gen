import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

parse_job_status_enum = ENUM("queued", "processing", "completed", "failed", name="parse_job_status", create_type=True)


class ParseJob(Base):
    __tablename__ = "parse_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    parser_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parser_profiles.id"))
    status: Mapped[str] = mapped_column(parse_job_status_enum, nullable=False, default="queued")
    raw_markdown_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    structured_json_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
