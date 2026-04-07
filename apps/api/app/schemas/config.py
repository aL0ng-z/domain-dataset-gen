import uuid
from datetime import datetime

from pydantic import BaseModel

from domain.schemas import BaseSchema


# --- ModelConfig ---
class ModelConfigCreate(BaseModel):
    name: str
    provider: str
    base_url: str
    api_key: str
    model_name: str
    temperature: float | None = None
    max_tokens: int | None = None
    extra_params: dict | None = None


class ModelConfigUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra_params: dict | None = None


class ModelConfigResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    is_default: bool
    provider: str
    base_url: str
    model_name: str
    temperature: float | None
    max_tokens: int | None
    extra_params: dict | None
    created_at: datetime
    updated_at: datetime


# --- ParserProfile ---
class ParserProfileCreate(BaseModel):
    name: str
    parser_name: str = "mock"
    parser_options: dict | None = None


class ParserProfileUpdate(BaseModel):
    name: str | None = None
    parser_name: str | None = None
    parser_options: dict | None = None


class ParserProfileResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    is_default: bool
    parser_name: str
    parser_options: dict | None
    created_at: datetime
    updated_at: datetime


# --- ChunkProfile ---
class ChunkProfileCreate(BaseModel):
    name: str
    strategy: str = "hybrid_heading_recursive"
    max_tokens: int = 512
    overlap_tokens: int = 50
    options: dict | None = None


class ChunkProfileUpdate(BaseModel):
    name: str | None = None
    strategy: str | None = None
    max_tokens: int | None = None
    overlap_tokens: int | None = None
    options: dict | None = None


class ChunkProfileResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    is_default: bool
    strategy: str
    max_tokens: int
    overlap_tokens: int
    options: dict | None
    created_at: datetime
    updated_at: datetime


# --- ExportProfile ---
class ExportProfileCreate(BaseModel):
    name: str
    format: str = "sft_jsonl"
    template_options: dict | None = None


class ExportProfileUpdate(BaseModel):
    name: str | None = None
    format: str | None = None
    template_options: dict | None = None


class ExportProfileResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    is_default: bool
    format: str
    template_options: dict | None
    created_at: datetime
    updated_at: datetime


# --- TaskPolicy ---
class TaskPolicyCreate(BaseModel):
    name: str
    task_type: str
    max_retries: int = 3
    timeout_seconds: int = 300
    concurrency_limit: int = 5


class TaskPolicyUpdate(BaseModel):
    name: str | None = None
    task_type: str | None = None
    max_retries: int | None = None
    timeout_seconds: int | None = None
    concurrency_limit: int | None = None


class TaskPolicyResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    is_default: bool
    task_type: str
    max_retries: int
    timeout_seconds: int
    concurrency_limit: int
    created_at: datetime
    updated_at: datetime
