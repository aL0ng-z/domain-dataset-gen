"""生成输出 schema 的基础合同。"""

import pytest

from app.generation.output_validation import (
    OutputContentError,
    OutputSchemaError,
    build_effective_output_schema,
    validate_generated_content,
    validate_template_output_schema,
)


def test_task_type_base_schema_rejects_array_and_blank_required_fields():
    schema = build_effective_output_schema("qa_generation", None)
    with pytest.raises(OutputContentError):
        validate_generated_content("qa_generation", schema, [])
    with pytest.raises(OutputContentError):
        validate_generated_content("qa_generation", schema, {"question": " ", "answer": "有效"})


def test_template_schema_is_frozen_and_enforced_with_base_fields():
    configured = {
        "type": "object",
        "required": ["difficulty"],
        "properties": {"difficulty": {"enum": ["easy", "medium", "hard"]}},
    }
    frozen = build_effective_output_schema("qa_generation", configured)
    assert validate_generated_content(
        "qa_generation",
        frozen,
        {"question": "问题", "answer": "答案", "difficulty": "easy"},
    )["difficulty"] == "easy"
    with pytest.raises(OutputContentError):
        validate_generated_content("qa_generation", frozen, {"question": "问题", "answer": "答案"})


def test_template_schema_rejects_external_reference_and_unknown_task_type():
    with pytest.raises(OutputSchemaError):
        validate_template_output_schema("qa_generation", {"$ref": "https://example.test/schema.json"})
    with pytest.raises(OutputSchemaError):
        build_effective_output_schema("unknown", None)
