"""生成输出的冻结 JSON Schema 与任务类型基础门禁。"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import SchemaError


class OutputSchemaError(ValueError):
    """PromptTemplate 配置的 output_schema 不可安全执行。"""


class OutputContentError(ValueError):
    """LLM 输出未满足冻结 schema 或任务类型最低内容要求。"""


_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "qa_generation": ("question", "answer"),
    "knowledge_extraction": ("title", "content"),
    "benchmark_case": ("question", "reference_answer"),
}


def _base_schema(task_type: str) -> dict[str, Any]:
    fields = _REQUIRED_FIELDS.get(task_type)
    if fields is None:
        raise OutputSchemaError(f"不支持的生成任务类型: {task_type}")
    return {
        "$schema": _DRAFT_2020_12,
        "type": "object",
        "required": list(fields),
        "properties": {field: {"type": "string", "minLength": 1} for field in fields},
    }


def _reject_external_refs(value: Any) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            raise OutputSchemaError("output_schema 仅支持本地 $ref")
        for child in value.values():
            _reject_external_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_external_refs(child)


def validate_template_output_schema(task_type: str, output_schema: dict[str, Any] | None) -> None:
    """验证模板配置，并要求其可和任务类型基础 schema 合并。"""
    _base_schema(task_type)
    if output_schema is None:
        return
    if not isinstance(output_schema, dict):
        raise OutputSchemaError("output_schema 必须是 JSON 对象")
    declared_draft = output_schema.get("$schema")
    if declared_draft is not None and declared_draft != _DRAFT_2020_12:
        raise OutputSchemaError("output_schema 仅支持 JSON Schema draft 2020-12")
    _reject_external_refs(output_schema)
    try:
        Draft202012Validator.check_schema(output_schema)
    except SchemaError as exc:
        raise OutputSchemaError(f"output_schema 非法: {exc.message}") from exc


def build_effective_output_schema(task_type: str, output_schema: dict[str, Any] | None) -> dict[str, Any]:
    """将任务类型基础要求与模板配置冻结成单一可执行 schema。"""
    validate_template_output_schema(task_type, output_schema)
    base = _base_schema(task_type)
    if output_schema is None:
        return base
    return {"$schema": _DRAFT_2020_12, "allOf": [base, output_schema]}


def validate_generated_content(task_type: str, effective_schema: dict[str, Any], content: Any) -> dict[str, Any]:
    """验证已解析 JSON 与批次冻结 schema；成功时返回原对象。"""
    if not isinstance(effective_schema, dict):
        raise OutputContentError("冻结 output_schema 缺失或不是对象")
    try:
        Draft202012Validator(effective_schema).validate(content)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        suffix = f"（字段 {location}）" if location else ""
        raise OutputContentError(f"生成内容不满足输出 schema{suffix}: {exc.message}") from exc
    if not isinstance(content, dict):
        raise OutputContentError("生成内容必须是 JSON 对象")
    for field in _REQUIRED_FIELDS[task_type]:
        value = content.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OutputContentError(f"生成内容字段 {field} 必须是非空字符串")
    return content
