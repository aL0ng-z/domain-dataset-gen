import uuid
from datetime import datetime

from domain.schemas import BaseSchema, RequestSchema


# --- Dataset ---
class DatasetCreate(RequestSchema):
    name: str
    description: str | None = None


class DatasetUpdate(RequestSchema):
    name: str | None = None
    description: str | None = None


class DatasetResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DatasetItemAdd(RequestSchema):
    curated_item_id: uuid.UUID


class DatasetItemResponse(BaseSchema):
    id: uuid.UUID
    dataset_id: uuid.UUID
    curated_item_id: uuid.UUID
    ordinal: int


# --- Benchmark ---
class BenchmarkCreate(RequestSchema):
    name: str
    description: str | None = None


class BenchmarkUpdate(RequestSchema):
    name: str | None = None
    description: str | None = None


class BenchmarkResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None
    status: str
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class BenchmarkCaseAdd(RequestSchema):
    curated_item_id: uuid.UUID


class BenchmarkCaseResponse(BaseSchema):
    id: uuid.UUID
    benchmark_id: uuid.UUID
    curated_item_id: uuid.UUID
    ordinal: int
