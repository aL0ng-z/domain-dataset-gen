"""全局异常映射：把异常/HTTPException 统一转换为 ErrorResponse / ValidationErrorResponse。

- 应用业务错误统一为 `ErrorResponse`：`{"code": ..., "message": ..., "context": ..., "request_id": ...}`。
- Pydantic/FastAPI 字段校验错误（422）保留为 `ValidationErrorResponse`（含 loc/msg/type）。
- 所有处理器把 request_id 写入日志并在响应中返回，便于前后端日志关联。
- 响应绝不包含堆栈、SQL、Token 或跨项目对象信息。
"""

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from domain.schemas import (
    STATUS_TO_ERROR_CODE,
    ErrorCode,
    ErrorResponse,
    ValidationErrorResponse,
)


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = _new_request_id()
        request.state.request_id = request_id
    return request_id


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    status_code = exc.status_code
    code: ErrorCode = STATUS_TO_ERROR_CODE.get(status_code, "INTERNAL_ERROR")

    # 透传 HTTPException 携带的头（如 WWW-Authenticate: Bearer），
    # 供 401 客户端按规范处理认证流程（任务卡 §4/T04）。
    headers = dict(exc.headers or {})

    # 保持 422 校验结构不变（FastAPI 默认路径会走 RequestValidationError 处理器）。
    if status_code == 422:
        detail_items = exc.detail if isinstance(exc.detail, list) else []
        # 业务路由显式抛出的 422（如 ParserProfileService 的 invalid_parser_endpoint）
        # detail 是字符串；保留其消息供前端展示（errors 列表为空）。
        message = "请求参数校验失败"
        if not detail_items and isinstance(exc.detail, str):
            message = exc.detail
        return JSONResponse(
            status_code=status_code,
            headers=headers,
            content={
                "code": "VALIDATION_ERROR",
                "message": message,
                "request_id": _request_id(request),
                "errors": [
                    {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
                    for e in detail_items
                ],
            },
        )

    # 业务/鉴权错误统一 envelope。
    detail = exc.detail
    if isinstance(detail, dict):
        # 允许 handler 直接指定 code/context（如 409 冲突）。
        code = detail.get("code", code)
        message = detail.get("message", detail.get("detail", "请求处理失败"))
        context = detail.get("context")
    else:
        message = str(detail) if detail else "请求处理失败"
        context = None

    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=ErrorResponse(
            code=code,
            message=message,
            context=context,
            request_id=_request_id(request),
        ).model_dump(),
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        errors.append(
            {
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    body = ValidationErrorResponse(
        message="请求参数校验失败",
        request_id=_request_id(request),
        errors=errors,
    ).model_dump()
    return JSONResponse(status_code=422, content=body)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = _request_id(request)
    # 不把内部异常细节写入响应；日志记录真实堆栈。
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code="INTERNAL_ERROR",
            message="服务器内部错误",
            context=None,
            request_id=request_id,
        ).model_dump(),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
