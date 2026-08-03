"""OpenAPI 定制：把统一错误 envelope 注入每个操作的响应声明。

FastAPI 默认只把 response_model 对应的 200/201/202 响应写进 OpenAPI，
不会声明 401/403/404/409/422/500。为了让前端从 OpenAPI 生成稳定的
错误类型（ErrorResponse / ValidationErrorResponse），这里在默认 schema
之上补充：

- components.schemas.ErrorResponse / ValidationErrorResponse / ValidationErrorItem
- 每个操作按安全与路由语义注入标准错误响应：
  * 带 security 的操作 -> 401 (ErrorResponse: AUTH_REQUIRED)
  * 路径含资源 id 的读/改/删操作 -> 404 (ErrorResponse: NOT_FOUND)
  * 需要角色或项目成员权限的操作 -> 403 (ErrorResponse: PERMISSION_DENIED)
  * 冲突语义操作（lease 等）-> 409 (ErrorResponse: CONFLICT)
  * 所有操作 -> 422 (ValidationErrorResponse) 与 500 (ErrorResponse: INTERNAL_ERROR)

生成的 OpenAPI 是前端类型与漂移检查的唯一事实源；任何新增领域 code 必须在
ErrorResponse.code 联合中登记并补充合同测试。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from domain.schemas import ERROR_CODES

_ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}
_VALIDATION_REF = {"$ref": "#/components/schemas/ValidationErrorResponse"}


def _response(description: str, schema_ref: dict[str, str]) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": schema_ref}},
    }


def _inject_error_responses(op: dict[str, Any], path: str, method: str) -> None:
    responses = op.setdefault("responses", {})
    has_security = bool(op.get("security"))
    resource_placeholders = ("{id}", "{cid}", "{did}", "{bid}", "{tid}", "{uid}", "{sid}", "{vid}", "{eid}", "{iid}", "{jid}")
    # 404 只对读/改/删这类“查找资源”的操作注入；创建（POST 到集合）不注入。
    has_resource_id = any(p in path for p in resource_placeholders) and method in ("get", "patch", "delete")

    # 401：受保护操作。
    if has_security and "401" not in responses:
        responses["401"] = _response("认证失败", _ERROR_REF)
    # 403：权限不足（带 security 的操作都可能触发）。
    if has_security and "403" not in responses:
        responses["403"] = _response("权限不足", _ERROR_REF)
    # 404：资源查找。
    if has_resource_id and "404" not in responses:
        responses["404"] = _response("资源不存在", _ERROR_REF)
    # 409：冲突语义（租约、合并、终审、内容版本冲突）。
    opid = str(op.get("operationId", ""))
    conflict_opid = (
        "lease" in opid
        or "conflict" in opid
        or "merge" in opid
        or "final_review" in opid
        or "section_update" in opid
        or "section_submit" in opid
        # T09：Candidate 审核/提升、CuratedItem 修订/审批均可能返回 409。
        or "candidate_review" in opid
        or "promote_to_curated" in opid
        or "curated_item_update" in opid
        or "curated_item_review" in opid
    )
    if conflict_opid and "409" not in responses:
        responses["409"] = _response("冲突", _ERROR_REF)
    # 422：字段校验。始终覆盖为 ValidationErrorResponse（FastAPI 默认注入的是
    # HTTPValidationError，前端需用统一可判别类型）。
    responses["422"] = _response("请求参数校验失败", _VALIDATION_REF)
    # 500：内部错误。
    if "500" not in responses:
        responses["500"] = _response("服务器内部错误", _ERROR_REF)


def build_custom_openapi(app: FastAPI) -> dict[str, Any]:
    """构建注入错误 envelope 的 OpenAPI。"""
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schemas = schema["components"]["schemas"]

    # 把错误模型写入 components，供前端生成稳定类型。
    schemas.setdefault(
        "ErrorResponse",
        {
            "title": "ErrorResponse",
            "type": "object",
            "properties": {
                "code": {
                    "title": "Code",
                    "type": "string",
                    "enum": list(ERROR_CODES),
                    "description": "稳定大写 snake case 业务错误码，前端按 code 分支",
                },
                "message": {"title": "Message", "type": "string", "description": "面向用户的说明，可本地化；不可作为前端控制流"},
                "context": {
                    "title": "Context",
                    "anyOf": [{"type": "object"}, {"type": "null"}],
                    "description": "仅含经 schema 声明的非敏感结构；允许为 null",
                },
                "request_id": {"title": "Request Id", "anyOf": [{"type": "string"}, {"type": "null"}], "description": "日志关联 ID"},
            },
            "required": ["code", "message"],
        },
    )
    schemas.setdefault(
        "ValidationErrorItem",
        {
            "title": "ValidationErrorItem",
            "type": "object",
            "properties": {
                "loc": {"title": "Loc", "type": "array", "items": {}, "description": "字段位置"},
                "msg": {"title": "Msg", "type": "string"},
                "type": {"title": "Type", "type": "string"},
            },
            "required": ["loc", "msg", "type"],
        },
    )
    schemas.setdefault(
        "ValidationErrorResponse",
        {
            "title": "ValidationErrorResponse",
            "type": "object",
            "properties": {
                "code": {"title": "Code", "type": "string", "const": "VALIDATION_ERROR"},
                "message": {"title": "Message", "type": "string"},
                "request_id": {"title": "Request Id", "anyOf": [{"type": "string"}, {"type": "null"}]},
                "errors": {"title": "Errors", "type": "array", "items": {"$ref": "#/components/schemas/ValidationErrorItem"}},
            },
            "required": ["code", "message", "errors"],
        },
    )

    # 为每个操作注入标准错误响应。
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            _inject_error_responses(op, path, method.lower())

    # FastAPI 默认注入的 HTTPValidationError 已被 ValidationErrorResponse 取代，
    # 不再被任何响应引用时移除，避免前端生成冗余/混淆类型。
    if "HTTPValidationError" in schemas:
        raw = json.dumps(schema)
        if "#/components/schemas/HTTPValidationError" not in raw:
            schemas.pop("HTTPValidationError")

    return schema
