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
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

dataset_status_enum = ENUM("draft", "finalized", name="dataset_status", create_type=True)
benchmark_status_enum = ENUM("draft", "finalized", name="benchmark_status", create_type=True)

#: T10：composition canonicalization 版本标识（写入 composition_canonicalization_version）。
COMPOSITION_CJSON_VERSION = "composition-cjson-v1"


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        # T10：finalized 时必须记录当时 composition revision/hash/审核人/时间，
        # 且与最终 composition 字段一致（一次性 finalize，不提供 reopen）。
        CheckConstraint(
            "status != 'finalized' OR ("
            "finalized_revision IS NOT NULL AND finalized_sha256 IS NOT NULL "
            "AND finalized_canonicalization_version IS NOT NULL "
            "AND finalized_by IS NOT NULL AND finalized_at IS NOT NULL "
            "AND finalized_revision = composition_revision "
            "AND finalized_sha256 = composition_sha256"
            ")",
            name="ck_datasets_finalized_complete",
        ),
        CheckConstraint(
            "status != 'draft' OR ("
            "finalized_revision IS NULL AND finalized_sha256 IS NULL "
            "AND finalized_canonicalization_version IS NULL "
            "AND finalized_by IS NULL AND finalized_at IS NULL"
            ")",
            name="ck_datasets_draft_no_finalize",
        ),
        CheckConstraint(
            "composition_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_datasets_composition_sha256_format",
        ),
        CheckConstraint(
            "finalized_sha256 IS NULL OR finalized_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_datasets_finalized_sha256_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(dataset_status_enum, nullable=False, default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # T10：composition 版本化哈希（draft 也持续维护，finalize 时作为期望值）。
    composition_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    composition_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: "0" * 64, server_default=text("repeat('0'::text, 64)")
    )
    composition_canonicalization_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default=COMPOSITION_CJSON_VERSION, server_default=text("'composition-cjson-v1'")
    )
    # T10：finalized 快照（一次性，不回滚）。
    finalized_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finalized_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finalized_canonicalization_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DatasetItem(Base):
    __tablename__ = "dataset_items"
    __table_args__ = (
        # T10：同一 CuratedItem 与 ordinal 在容器内唯一（并发追加安全性）。
        UniqueConstraint("dataset_id", "curated_item_id", name="uq_dataset_items_dataset_item"),
        UniqueConstraint("dataset_id", "ordinal", name="uq_dataset_items_dataset_ordinal"),
        # T10：ordinal 必须为正整数。
        CheckConstraint("ordinal >= 1", name="ck_dataset_items_ordinal_positive"),
        # T10：保存的 hash 必须为 64 位小写十六进制 SHA-256。
        CheckConstraint(
            "curated_revision_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_items_revision_sha256_format",
        ),
        CheckConstraint(
            "approval_evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_items_evidence_sha256_format",
        ),
        Index("ix_dataset_items_dataset_ordinal", "dataset_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"))
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # T10：固定加入时 T09 的批准 revision/approval record（不可变，不跟随当前内容漂移）。
    curated_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curated_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    curated_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_records.id", ondelete="RESTRICT"), nullable=False
    )
    approval_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Benchmark(Base):
    __tablename__ = "benchmarks"
    __table_args__ = (
        CheckConstraint(
            "status != 'finalized' OR ("
            "finalized_revision IS NOT NULL AND finalized_sha256 IS NOT NULL "
            "AND finalized_canonicalization_version IS NOT NULL "
            "AND finalized_by IS NOT NULL AND finalized_at IS NOT NULL "
            "AND finalized_revision = composition_revision "
            "AND finalized_sha256 = composition_sha256"
            ")",
            name="ck_benchmarks_finalized_complete",
        ),
        CheckConstraint(
            "status != 'draft' OR ("
            "finalized_revision IS NULL AND finalized_sha256 IS NULL "
            "AND finalized_canonicalization_version IS NULL "
            "AND finalized_by IS NULL AND finalized_at IS NULL"
            ")",
            name="ck_benchmarks_draft_no_finalize",
        ),
        CheckConstraint(
            "composition_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_benchmarks_composition_sha256_format",
        ),
        CheckConstraint(
            "finalized_sha256 IS NULL OR finalized_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_benchmarks_finalized_sha256_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(benchmark_status_enum, nullable=False, default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # T10：composition 版本化哈希。
    composition_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    composition_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: "0" * 64, server_default=text("repeat('0'::text, 64)")
    )
    composition_canonicalization_version: Mapped[str] = mapped_column(
        String(50), nullable=False, default=COMPOSITION_CJSON_VERSION, server_default=text("'composition-cjson-v1'")
    )
    # T10：finalized 快照（一次性，不回滚）。
    finalized_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finalized_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finalized_canonicalization_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BenchmarkCase(Base):
    __tablename__ = "benchmark_cases"
    __table_args__ = (
        UniqueConstraint("benchmark_id", "curated_item_id", name="uq_benchmark_cases_benchmark_case"),
        UniqueConstraint("benchmark_id", "ordinal", name="uq_benchmark_cases_benchmark_ordinal"),
        CheckConstraint("ordinal >= 1", name="ck_benchmark_cases_ordinal_positive"),
        CheckConstraint(
            "curated_revision_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_benchmark_cases_revision_sha256_format",
        ),
        CheckConstraint(
            "approval_evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_benchmark_cases_evidence_sha256_format",
        ),
        Index("ix_benchmark_cases_benchmark_ordinal", "benchmark_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    benchmark_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("benchmarks.id", ondelete="CASCADE"))
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # T10：固定加入时 T09 的批准 revision/approval record（不可变）。
    curated_revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("curated_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    curated_revision_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_records.id", ondelete="RESTRICT"), nullable=False
    )
    approval_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
