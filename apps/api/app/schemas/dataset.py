import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


# --- Dataset ---
class DatasetCreate(BaseModel):
    name: str
    description: str | None = None


class DatasetUpdate(BaseModel):
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


class DatasetItemAdd(BaseModel):
    curated_item_id: uuid.UUID


class DatasetItemResponse(BaseSchema):
    id: uuid.UUID
    dataset_id: uuid.UUID
    curated_item_id: uuid.UUID
    ordinal: int


# --- Benchmark ---
class BenchmarkCreate(BaseModel):
    name: str
    description: str | None = None


class BenchmarkUpdate(BaseModel):
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


class BenchmarkCaseAdd(BaseModel):
    curated_item_id: uuid.UUID


class BenchmarkCaseResponse(BaseSchema):
    id: uuid.UUID
    benchmark_id: uuid.UUID
    curated_item_id: uuid.UUID
    ordinal: int
