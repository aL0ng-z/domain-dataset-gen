"""导出类型与必需字段门禁：API 预检、封存及快照渲染共用。"""

from __future__ import annotations

from typing import Any

EXPORT_FORMATS = frozenset({"sft_jsonl", "qa_json", "messages", "alpaca", "sharegpt", "benchmark_json"})


class ExportContentError(ValueError):
    def __init__(self, code: str, issues: list[dict[str, Any]]):
        self.code = code
        self.issues = issues
        super().__init__(
            "所选格式不支持部分条目类型" if code == "EXPORT_FORMAT_INCOMPATIBLE"
            else "部分导出条目缺少有效的必需字段"
        )


def validate_export_members(members: list[dict], fmt: str) -> list[dict]:
    """校验全部固定 revision；返回供格式化器使用的内容副本，不修改原快照。

    每个成员提供 curated_item.id/type 和 curated_revision.content。
    question/answer 字段存在时优先使用，只有缺失时才兼容 instruction/output。
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}")
    if not members:
        raise ExportContentError("EXPORT_CONTENT_INVALID", [{"fields": ["members"], "reason": "没有可导出的条目"}])
    issues = []
    normalized = []
    for member in members:
        item = member.get("curated_item") or {}
        content = (member.get("curated_revision") or {}).get("content")
        item_type = item.get("type")
        issue = {"curated_item_id": str(item.get("id", "")), "item_type": item_type}
        if item_type != "qa_generation" and not (item_type == "benchmark_case" and fmt == "benchmark_json"):
            issues.append({**issue, "code": "EXPORT_FORMAT_INCOMPATIBLE", "fields": ["type"], "reason": f"{item_type} 不支持 {fmt}"})
            continue
        if not isinstance(content, dict):
            issues.append({**issue, "code": "EXPORT_CONTENT_INVALID", "fields": ["content"], "reason": "内容必须为对象"})
            continue
        if item_type == "benchmark_case":
            question_field, answer_field = "question", "reference_answer"
        else:
            question_field = "question" if "question" in content else "instruction"
            answer_field = "answer" if "answer" in content else "output"
        invalid = [
            field
            for field in (question_field, answer_field)
            if not isinstance(content.get(field), str) or not content[field].strip()
        ]
        # ``input`` 是可选的，但一旦提供必须是原样可导出的字符串。否则 messages /
        # ShareGPT 无法可靠地把它合入用户消息，不能静默丢弃或转成字符串。
        if "input" in content and not isinstance(content["input"], str):
            invalid.append("input")
        if invalid:
            issues.append({**issue, "code": "EXPORT_CONTENT_INVALID", "fields": invalid, "reason": "必需字段必须为非空字符串"})
            continue
        question, answer = content[question_field], content[answer_field]
        normalized.append({
            **member,
            "curated_revision": {**member["curated_revision"], "content": {
                **content, "question": question, "instruction": question,
                "answer": answer, "output": answer, "reference_answer": answer,
            }},
        })
    if issues:
        code = "EXPORT_FORMAT_INCOMPATIBLE" if any(i["code"] == "EXPORT_FORMAT_INCOMPATIBLE" for i in issues) else "EXPORT_CONTENT_INVALID"
        raise ExportContentError(code, issues)
    return normalized
