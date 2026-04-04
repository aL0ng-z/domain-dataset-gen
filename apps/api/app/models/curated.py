import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

curated_item_status_enum = ENUM("draft", "approved", "exported", "deprecated", name="curated_item_status", create_type=True)


class CuratedItem(Base):
    __tablename__ = "curated_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id"))
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(curated_item_status_enum, nullable=False, default="draft")
    promoted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CuratedRevision(Base):
    __tablename__ = "curated_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id", ondelete="CASCADE"))
    revised_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    revision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceLink(Base):
    __tablename__ = "evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chunks.id"))
    source_pages: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
