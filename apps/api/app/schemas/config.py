import uuid
from datetime import datetime

from pydantic import BaseModel, field_serializer, field_validator

from app.security.snapshot import find_forbidden_keys, redact, validate_parser_options
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
PARSER_SECRET_KEYS = {"api_key", "access_token", "token"}


class ParserProfileCreate(BaseModel):
    name: str
    parser_name: str = "mock"
    parser_options: dict | None = None

    @field_validator("parser_options")
    @classmethod
    def _validate_parser_options(cls, options: dict | None) -> dict | None:
        if options is None:
            return None
        # 禁止字段检查必须递归、大小写不敏感并规范化连字符/下划线。
        forbidden = find_forbidden_keys(options)
        if forbidden:
            raise ValueError("unsafe_parser_option: 禁用字段")
        return options

    @field_validator("parser_name")
    @classmethod
    def _validate_parser_name(cls, parser_name: str) -> str:
        from parsing import AVAILABLE_PARSERS

        if parser_name not in AVAILABLE_PARSERS:
            raise ValueError(f"invalid_parser_name: {parser_name}")
        return parser_name


class ParserProfileUpdate(BaseModel):
    name: str | None = None
    parser_name: str | None = None
    parser_options: dict | None = None

    @field_validator("parser_options")
    @classmethod
    def _validate_parser_options(cls, options: dict | None) -> dict | None:
        if options is None:
            return None
        forbidden = find_forbidden_keys(options)
        if forbidden:
            raise ValueError("unsafe_parser_option: 禁用字段")
        return options

    @field_validator("parser_name")
    @classmethod
    def _validate_parser_name(cls, parser_name: str | None) -> str | None:
        if parser_name is None:
            return None
        from parsing import AVAILABLE_PARSERS

        if parser_name not in AVAILABLE_PARSERS:
            raise ValueError(f"invalid_parser_name: {parser_name}")
        return parser_name


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
        return redact(dict(options))


class ParserEndpointOption(BaseModel):
    """项目用户可选的端点只读信息；不返回主机/IP/端口/allowlist 或网络区域内部细节。"""

    endpoint_ref: str
    display_name: str
    parser_name: str
    credential_configured: bool


class ParserEndpointListResponse(BaseSchema):
    items: list[ParserEndpointOption]


# 供 CRUD 服务在创建/更新时执行白名单校验的辅助函数。
def finalize_parser_options(parser_name: str, options: dict | None) -> dict | None:
    """按 parser_name 白名单收紧 parser_options；拒绝未知字段与网络/秘密字段。"""
    return validate_parser_options(parser_name, options)


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
