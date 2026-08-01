from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class BaseSchema(BaseModel):
    """领域响应模型基类：允许从 ORM 属性构建。"""

    model_config = ConfigDict(from_attributes=True)


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
