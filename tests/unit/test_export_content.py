"""R17：检查训练文件的正文语义，而非只检查 hash 或产物存在。"""

import json

import pytest

from app.workers.export_worker import render_payload_from_manifest
from domain.export_content import EXPORT_FORMATS, ExportContentError, validate_export_members


def member(item_type="qa_generation", content=None, item_id="item-1"):
    return {
        "curated_item": {"id": item_id, "type": item_type},
        "curated_revision": {"content": content if content is not None else {"question": "问题", "answer": "答案"}},
    }


@pytest.mark.parametrize("fmt", sorted(EXPORT_FORMATS))
@pytest.mark.parametrize("item_type", ["qa_generation", "benchmark_case", "knowledge_extraction"])
def test_default_type_format_matrix(fmt, item_type):
    content = {
        "qa_generation": {"question": "问题", "answer": "答案"},
        "benchmark_case": {"question": "问题", "reference_answer": "答案"},
        "knowledge_extraction": {"title": "知识", "summary": "摘要", "details": "正文"},
    }[item_type]
    manifest = {"members": [member(item_type, content)]}
    if item_type == "qa_generation" or (item_type == "benchmark_case" and fmt == "benchmark_json"):
        payload = render_payload_from_manifest(manifest, fmt)
        assert "问题" in payload and "答案" in payload
        if fmt == "benchmark_json":
            assert json.loads(payload)[0]["reference_answer"] == "答案"
    else:
        with pytest.raises(ExportContentError) as exc:
            render_payload_from_manifest(manifest, fmt)
        assert exc.value.code == "EXPORT_FORMAT_INCOMPATIBLE"
        assert exc.value.issues[0]["curated_item_id"] == "item-1"


@pytest.mark.parametrize("fmt", sorted(EXPORT_FORMATS))
def test_question_answer_take_precedence_and_legacy_pair_is_supported(fmt):
    explicit = member(content={"question": "新问题", "answer": "新答案", "instruction": "旧问题", "output": "旧答案"})
    payload = render_payload_from_manifest({"members": [explicit]}, fmt)
    assert "新问题" in payload and "新答案" in payload
    assert "旧问题" not in payload and "旧答案" not in payload
    legacy = member(content={"instruction": "兼容问题", "output": "兼容答案"})
    payload = render_payload_from_manifest({"members": [legacy]}, fmt)
    assert "兼容问题" in payload and "兼容答案" in payload
    assert explicit["curated_revision"]["content"]["instruction"] == "旧问题"


@pytest.mark.parametrize("invalid", [None, "", "  \n", [], {}, 0, False])
def test_invalid_present_answer_cannot_fallback_to_old_output(invalid):
    with pytest.raises(ExportContentError) as exc:
        validate_export_members([member(content={"question": "Q", "answer": invalid, "output": "旧正文"})], "qa_json")
    assert exc.value.code == "EXPORT_CONTENT_INVALID"
    assert exc.value.issues[0]["fields"] == ["answer"]


def test_mixed_invalid_batch_reports_all_issues_without_filtering():
    members = [member(), member("knowledge_extraction", {}, "knowledge"), member(content={}, item_id="empty")]
    with pytest.raises(ExportContentError) as exc:
        validate_export_members(members, "qa_json")
    assert exc.value.code == "EXPORT_FORMAT_INCOMPATIBLE"
    assert {i["curated_item_id"] for i in exc.value.issues} == {"knowledge", "empty"}


def test_benchmark_requires_reference_answer_and_unknown_format_is_invalid():
    with pytest.raises(ExportContentError, match="必需字段"):
        validate_export_members([member("benchmark_case")], "benchmark_json")
    with pytest.raises(ValueError, match="不支持的导出格式"):
        validate_export_members([member()], "unknown")


@pytest.mark.parametrize("fmt", ["messages", "sharegpt"])
def test_messages_and_sharegpt_keep_nonempty_input(fmt):
    payload = render_payload_from_manifest(
        {"members": [member(content={"question": "问题", "input": "唯一上下文", "answer": "答案"})]},
        fmt,
    )
    record = json.loads(payload)[0]
    user_content = (
        record["messages"][1]["content"]
        if fmt == "messages"
        else record["conversations"][0]["value"]
    )
    assert user_content == "问题\n\n唯一上下文"


@pytest.mark.parametrize("invalid", [None, [], {}, 0])
def test_present_input_must_be_a_string(invalid):
    with pytest.raises(ExportContentError) as exc:
        validate_export_members(
            [member(content={"question": "问题", "input": invalid, "answer": "答案"})],
            "messages",
        )
    assert exc.value.code == "EXPORT_CONTENT_INVALID"
    assert exc.value.issues[0]["fields"] == ["input"]
