import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# T08：generation_run_status 增加 cancelled；新写状态机
# queued -> processing -> completed|failed|cancelled。
generation_run_status_enum = ENUM(
    "queued", "processing", "completed", "failed", "cancelled",
    name="generation_run_status", create_type=True,
)
candidate_status_enum = ENUM("ai_generated", "human_edited", "review_pending", "approved", "rejected", name="candidate_status", create_type=True)
review_verdict_enum = ENUM("supported", "partially_supported", "unsupported", "out_of_scope", name="review_verdict", create_type=True)


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        # T08：一个批次对一个 Chunk 最多一个 run。
        UniqueConstraint(
            "generation_batch_id", "chunk_id",
            name="uq_generation_runs_batch_chunk",
        ),
        # T08：provenance 约束。
        CheckConstraint(
            "is_legacy = true OR provenance_status = 'verified'",
            name="ck_generation_runs_verified_not_legacy",
        ),
        CheckConstraint(
            "provenance_status NOT IN ('legacy_unavailable', 'invalid') OR "
            "(is_legacy = true AND provenance_error_code IS NOT NULL)",
            name="ck_generation_runs_legacy_unavailable_error",
        ),
        CheckConstraint(
            "provenance_status IN ('verified', 'legacy_unavailable', 'invalid')",
            name="ck_generation_runs_provenance_status_valid",
        ),
        # T08：verified Run 必须关联 batch 且已渲染 prompt + hash
        # （无论是否 legacy，provenance=verified 时都强制）。
        CheckConstraint(
            "provenance_status != 'verified' OR ("
            "generation_batch_id IS NOT NULL "
            "AND input_prompt IS NOT NULL "
            "AND rendered_prompt_sha256 IS NOT NULL"
            ")",
            name="ck_generation_runs_verified_has_input",
        ),
        # 终态必须设置 completed_at；失败必须有 error_message，成功必须有 raw_output。
        # （legacy 迁移前行由迁移回填诚实标记，不强制新格式字段。）
        CheckConstraint(
            "is_legacy = true OR ("
            "status IN ('completed', 'failed', 'cancelled') = (completed_at IS NOT NULL)"
            ")",
            name="ck_generation_runs_terminal_completed_at",
        ),
        CheckConstraint(
            "is_legacy = true OR (status != 'failed' OR error_message IS NOT NULL)",
            name="ck_generation_runs_failed_has_error",
        ),
        CheckConstraint(
            "is_legacy = true OR (status != 'completed' OR raw_output IS NOT NULL)",
            name="ck_generation_runs_completed_has_output",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"))
    prompt_template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_templates.id"))
    model_config_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("model_configs.id"))
    context_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="single_chunk")
    input_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(generation_run_status_enum, nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- T08：与 GenerationBatch 关联（新 run 必填；仅兼容历史数据可空）----
    generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generation_batches.id", ondelete="RESTRICT"), nullable=True
    )
    rendered_prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- T08：legacy / provenance 审计 ----
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    provenance_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="verified")
    provenance_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        # T08：成功 run 最多产出一个 Candidate。
        UniqueConstraint("generation_run_id", name="uq_candidates_generation_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    generation_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_runs.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"))
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    candidate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(candidate_status_enum, nullable=False, default="ai_generated")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    review_verdict: Mapped[str | None] = mapped_column(review_verdict_enum, nullable=True)
    review_evidence_spans: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    source_generation_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("generation_batches.id"), nullable=True)
    thinking_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CandidateComment(Base):
    __tablename__ = "candidate_comments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
