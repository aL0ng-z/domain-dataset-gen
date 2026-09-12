import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.security.snapshot import find_forbidden_keys, redact, validate_parser_options
from domain.enums import TaskType
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

    @model_validator(mode="after")
    def _validate_options_whitelist(self) -> "ParserProfileCreate":
        """按 parser_name 白名单拒绝未知字段（422 字段级错误）。"""
        if self.parser_options is not None:
            validate_parser_options(self.parser_name, self.parser_options)
        return self


class ParserProfileUpdate(RequestSchema):
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

    @model_validator(mode="after")
    def _validate_options_whitelist(self) -> "ParserProfileUpdate":
        # Update 场景：parser_name 可能未传；白名单校验在 service.update 读到
        # 既有 parser_name 后再执行一次（此处仅当显式传 parser_name 时校验）。
        if self.parser_options is not None and self.parser_name is not None:
            validate_parser_options(self.parser_name, self.parser_options)
        return self


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
class ChunkProfileCreate(RequestSchema):
    name: str
    strategy: str = "hybrid_heading_recursive"
    max_tokens: int = 512
    overlap_tokens: int = 50
    options: dict | None = None

    # T06 §4.2：Pydantic 与 DB CHECK 同一规则。
    @model_validator(mode="after")
    def _validate_token_budget(self) -> "ChunkProfileCreate":
        if self.max_tokens <= 0:
            raise ValueError("max_tokens 必须大于 0")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens 必须 >= 0")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens 必须小于 max_tokens")
        return self


class ChunkProfileUpdate(RequestSchema):
    name: str | None = None
    strategy: str | None = None
    max_tokens: int | None = None
    overlap_tokens: int | None = None
    options: dict | None = None

    # T06 §4.2：仅当两个字段都提供时校验组合约束（部分更新）。
    @model_validator(mode="after")
    def _validate_token_budget(self) -> "ChunkProfileUpdate":
        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError("max_tokens 必须大于 0")
        if self.overlap_tokens is not None and self.overlap_tokens < 0:
            raise ValueError("overlap_tokens 必须 >= 0")
        if (
            self.max_tokens is not None
            and self.overlap_tokens is not None
            and self.overlap_tokens >= self.max_tokens
        ):
            raise ValueError("overlap_tokens 必须小于 max_tokens")
        return self


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
    task_type: TaskType
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=300, gt=0)
    concurrency_limit: int = Field(default=5, gt=0)


class TaskPolicyUpdate(RequestSchema):
    name: str | None = None
    task_type: TaskType | None = None
    max_retries: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    concurrency_limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def reject_explicit_null(self):
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} 不可为 null")
        return self


class TaskPolicyResponse(BaseSchema):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: int
    is_default: bool
    task_type: TaskType
    max_retries: int
    timeout_seconds: int
    concurrency_limit: int
    created_at: datetime
    updated_at: datetime
