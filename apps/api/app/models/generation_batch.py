import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

generation_batch_status_enum = ENUM(
    "pending", "processing", "review_pending", "completed", "failed",
    name="generation_batch_status", create_type=True,
)


class GenerationBatch(Base):
    __tablename__ = "generation_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chunk_sets.id"), nullable=True)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=True)
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_templates.id"), nullable=True)
    selected_chunk_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(generation_batch_status_enum, nullable=False, default="pending")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
