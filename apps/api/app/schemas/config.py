import uuid
from datetime import datetime

from pydantic import field_serializer, field_validator

from domain.schemas import BaseSchema, RequestSchema


# --- ModelConfig ---
class ModelConfigCreate(RequestSchema):
    name: str
    provider: str
    base_url: str
    api_key: str
    model_name: str
    temperature: float | None = None
    max_tokens: int | None = None
    extra_params: dict | None = None


class ModelConfigUpdate(RequestSchema):
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
PARSER_SECRET_KEYS = {"api_key", "access_token", "token"}


def _validate_parser_options(options: dict | None) -> dict | None:
    if options and PARSER_SECRET_KEYS.intersection(options):
        raise ValueError("解析器 API 密钥必须配置在后端环境变量中，不能保存到 ParserProfile")
    return options


class ParserProfileCreate(RequestSchema):
    name: str
    parser_name: str = "mock"
    parser_options: dict | None = None

    _no_embedded_secrets = field_validator("parser_options")(_validate_parser_options)


class ParserProfileUpdate(RequestSchema):
    name: str | None = None
    parser_name: str | None = None
    parser_options: dict | None = None

    _no_embedded_secrets = field_validator("parser_options")(_validate_parser_options)


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

    @field_serializer("parser_options")
    def redact_parser_options(self, options: dict | None) -> dict | None:
        if not options:
            return options
        sanitized = dict(options)
        had_secret = any(sanitized.pop(key, None) is not None for key in PARSER_SECRET_KEYS)
        if had_secret:
            sanitized["credential_configured"] = True
        return sanitized


# --- ChunkProfile ---
class ChunkProfileCreate(RequestSchema):
    name: str
    strategy: str = "hybrid_heading_recursive"
    max_tokens: int = 512
    overlap_tokens: int = 50
    options: dict | None = None


class ChunkProfileUpdate(RequestSchema):
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
class ExportProfileCreate(RequestSchema):
    name: str
    format: str = "sft_jsonl"
    template_options: dict | None = None


class ExportProfileUpdate(RequestSchema):
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
class TaskPolicyCreate(RequestSchema):
    name: str
    task_type: str
    max_retries: int = 3
    timeout_seconds: int = 300
    concurrency_limit: int = 5


class TaskPolicyUpdate(RequestSchema):
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
