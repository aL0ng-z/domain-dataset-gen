from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class BaseSchema(BaseModel):
    """领域响应模型基类：允许从 ORM 属性构建。"""

    model_config = ConfigDict(from_attributes=True)


class RequestSchema(BaseModel):
    """请求模型基类：拒绝未声明的额外字段（返回 422），保证前端不会带旧字段静默通过。

    这是“请求字段在两端名称不同只能在运行时暴露”这一风险的关键防线：
    旧请求字段（如 template_id、字符串 evidence_spans）会被 Pydantic 以
    extra=forbid 拒绝，而不是被静默丢弃。
    """

    model_config = ConfigDict(extra="forbid")


class PaginatedResponse(BaseModel, Generic[T]):
    """统一分页响应结构：所有可增长集合使用 `{"items":[],"total":0,"page":1,"page_size":20}`。

    - `page >= 1`，`1 <= page_size <= 100`；非法值由路由 Query 约束返回 422。
    - `total` 是过滤条件生效后的总数，不是当前页长度；空页仍返回相同四个键。
    """

    items: list[T]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 统一错误 envelope
# ---------------------------------------------------------------------------
# 应用业务错误统一为 ErrorResponse；字段校验错误为 ValidationErrorResponse。
# `code` 使用稳定大写 snake case 并在 OpenAPI 中声明为有限联合；`message` 可
# 本地化、不可作为前端控制流。`request_id` 用于日志关联。
# 后续业务卡（T05-T11）可声明新的领域 code，但必须复用本 envelope。
#
# 注意：`context` 只能包含经 schema 声明的非敏感结构，允许为 null；响应不得
# 包含堆栈、SQL、Token 或跨项目对象信息。

ERROR_CODES = (
    "AUTH_REQUIRED",
    "PERMISSION_DENIED",
    "NOT_FOUND",
    "CONFLICT",
    "BAD_REQUEST",
    "PAYLOAD_TOO_LARGE",
    "VALIDATION_ERROR",
    "INTERNAL_ERROR",
    "SECTION_LEASE_HELD",
    "SECTION_LEASE_LOST",
    "SECTION_VERSION_CONFLICT",
    "CLEAN_SOURCE_CHANGED",
    "CLEAN_VERSION_NOT_READY",
    "CLEAN_VERSION_REVIEW_CONFLICT",
    "CLEAN_VERSION_STALE",
    # T06：版本化切分 API 合同错误。
    "IDEMPOTENCY_KEY_REUSED",
    "CHUNK_RUN_IN_PROGRESS",
    "CHUNK_SET_IMMUTABLE",
    # T08：生成链路 API 合同错误（表 §5.2 有限联合）。
    "GENERATION_CONFIG_NOT_FOUND",
    "GENERATION_SOURCE_NOT_FOUND",
    "GENERATION_CONFIG_UNAVAILABLE",
    "GENERATION_SNAPSHOT_UNSAFE",
    "GENERATION_RENDERER_UNAVAILABLE",
    "GENERATION_SOURCE_NOT_READY",
    "GENERATION_IN_PROGRESS",
    "GENERATION_NOT_RETRYABLE",
    "GENERATION_RETRY_EXISTS",
    "GENERATION_PROVENANCE_INVALID",
    # T09：Candidate/CuratedItem 证据与审批 API 合同错误（表 §5.2 有限联合）。
    "CANDIDATE_REVIEW_STATE_CONFLICT",
    "CANDIDATE_REVISION_CONFLICT",
    "CANDIDATE_EVIDENCE_REQUIRED",
    "CANDIDATE_ALREADY_PROMOTED",
    "DOCUMENT_IN_USE",
    "CURATED_REVISION_CONFLICT",
    "CURATED_APPROVAL_GATE_FAILED",
    "CURATED_REVIEW_STATE_CONFLICT",
    # T10：Dataset/Benchmark composition API 合同错误（表 §5.3 有限联合）。
    "COMPOSITION_NOT_DRAFT",
    "COMPOSITION_ITEM_INELIGIBLE",
    "COMPOSITION_MEMBER_EXISTS",
    "COMPOSITION_REVISION_CONFLICT",
    "COMPOSITION_FINALIZE_GATE_FAILED",
    "COMPOSITION_HASH_INVALID",
    # T11：导出 API 合同错误。
    "EXPORT_SOURCE_NOT_FINALIZED",
    "EXPORT_REVISION_CONFLICT",
    "EXPORT_IMMUTABLE",
    "EXPORT_INTEGRITY_ERROR",
    "EXPORT_PROVENANCE_GAP",
    "EXPORT_FORMAT_INCOMPATIBLE",
    "EXPORT_CONTENT_INVALID",
)

ErrorCode = Literal[
    "AUTH_REQUIRED",
    "PERMISSION_DENIED",
    "NOT_FOUND",
    "CONFLICT",
    "BAD_REQUEST",
    "PAYLOAD_TOO_LARGE",
    "VALIDATION_ERROR",
    "INTERNAL_ERROR",
    "SECTION_LEASE_HELD",
    "SECTION_LEASE_LOST",
    "SECTION_VERSION_CONFLICT",
    "CLEAN_SOURCE_CHANGED",
    "CLEAN_VERSION_NOT_READY",
    "CLEAN_VERSION_REVIEW_CONFLICT",
    "CLEAN_VERSION_STALE",
    "IDEMPOTENCY_KEY_REUSED",
    "CHUNK_RUN_IN_PROGRESS",
    "CHUNK_SET_IMMUTABLE",
    "GENERATION_CONFIG_NOT_FOUND",
    "GENERATION_SOURCE_NOT_FOUND",
    "GENERATION_CONFIG_UNAVAILABLE",
    "GENERATION_SNAPSHOT_UNSAFE",
    "GENERATION_RENDERER_UNAVAILABLE",
    "GENERATION_SOURCE_NOT_READY",
    "GENERATION_IN_PROGRESS",
    "GENERATION_NOT_RETRYABLE",
    "GENERATION_RETRY_EXISTS",
    "GENERATION_PROVENANCE_INVALID",
    "CANDIDATE_REVIEW_STATE_CONFLICT",
    "CANDIDATE_REVISION_CONFLICT",
    "CANDIDATE_EVIDENCE_REQUIRED",
    "CANDIDATE_ALREADY_PROMOTED",
    "DOCUMENT_IN_USE",
    "CURATED_REVISION_CONFLICT",
    "CURATED_APPROVAL_GATE_FAILED",
    "CURATED_REVIEW_STATE_CONFLICT",
    "COMPOSITION_NOT_DRAFT",
    "COMPOSITION_ITEM_INELIGIBLE",
    "COMPOSITION_MEMBER_EXISTS",
    "COMPOSITION_REVISION_CONFLICT",
    "COMPOSITION_FINALIZE_GATE_FAILED",
    "COMPOSITION_HASH_INVALID",
    "EXPORT_SOURCE_NOT_FINALIZED",
    "EXPORT_REVISION_CONFLICT",
    "EXPORT_IMMUTABLE",
    "EXPORT_INTEGRITY_ERROR",
    "EXPORT_PROVENANCE_GAP",
    "EXPORT_FORMAT_INCOMPATIBLE",
    "EXPORT_CONTENT_INVALID",
]

# HTTP 状态码 -> 稳定错误 code 映射（唯一事实源，供全局异常处理器使用）。
STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    400: "BAD_REQUEST",
    401: "AUTH_REQUIRED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
}


class ErrorResponse(BaseModel):
    """应用业务错误统一响应：前端按稳定 `code` 分支，不得匹配中文 message/detail。"""

    code: ErrorCode
    message: str
    context: dict[str, Any] | None = None
    request_id: str | None = None


class ValidationErrorItem(BaseModel):
    """字段校验错误的单条位置/原因。"""

    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorResponse(BaseModel):
    """Pydantic/FastAPI 字段校验错误（HTTP 422），保留字段位置与原因。"""

    code: Literal["VALIDATION_ERROR"] = "VALIDATION_ERROR"
    message: str
    request_id: str | None = None
    errors: list[ValidationErrorItem]
