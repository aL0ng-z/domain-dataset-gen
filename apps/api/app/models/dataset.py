import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

dataset_status_enum = ENUM("draft", "finalized", name="dataset_status", create_type=True)
benchmark_status_enum = ENUM("draft", "finalized", name="benchmark_status", create_type=True)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(dataset_status_enum, nullable=False, default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DatasetItem(Base):
    __tablename__ = "dataset_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"))
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class Benchmark(Base):
    __tablename__ = "benchmarks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(benchmark_status_enum, nullable=False, default="draft")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BenchmarkCase(Base):
    __tablename__ = "benchmark_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    benchmark_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("benchmarks.id", ondelete="CASCADE"))
    curated_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("curated_items.id"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
