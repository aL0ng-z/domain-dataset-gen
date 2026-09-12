import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

parse_job_status_enum = ENUM("queued", "processing", "completed", "failed", "cancelled", name="parse_job_status", create_type=True)


class ParseJob(Base):
    __tablename__ = "parse_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"))
    parser_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("parser_profiles.id"))
    status: Mapped[str] = mapped_column(parse_job_status_enum, nullable=False, default="queued")
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw_markdown_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    structured_json_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_mapping: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ---- T03: 不可变配置冻结快照 ----
    # snapshot_schema_version=0 表示 legacy_unavailable（迁移前已存在的历史 job）；
    # >=1 时两个 snapshot、两个 hash、endpoint_policy_ref/version 必须全部非空。
    # 以下字段由数据库触发器保护：创建后禁止 UPDATE 改写。
    snapshot_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    parser_profile_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parser_profile_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    endpoint_policy_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    endpoint_policy_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    endpoint_policy_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    endpoint_policy_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 冻结时间（审计）：创建时写入；数据库禁止改写。
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
