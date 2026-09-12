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

chunk_status_enum = ENUM("ready", "generating", "generated", name="chunk_status", create_type=True)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("chunk_set_id", "ordinal", name="uq_chunks_set_ordinal"),
        Index("ix_chunks_set_ordinal", "chunk_set_id", "ordinal"),
        Index("ix_chunks_doc_set", "document_id", "chunk_set_id"),
        CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_nonneg"),
        CheckConstraint("token_count > 0", name="ck_chunks_token_count_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_pages: Mapped[list[int] | dict | None] = mapped_column(JSONB, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(chunk_status_enum, nullable=False, default="ready")
    # T06：所有 Chunk 强制绑定一个 ChunkSet（迁移后 NOT NULL）。
    chunk_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
