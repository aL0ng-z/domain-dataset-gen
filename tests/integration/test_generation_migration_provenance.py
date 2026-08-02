"""T08 迁移 provenance 回填分类测试（验收标准 10）。

迁移的 Batch/Run provenance 分类是纯函数，通过 importlib 直接加载迁移模块复用：
- 缺少必要输入 -> legacy_unavailable；
- 发现互相矛盾的 FK/版本/内容 -> invalid；
- 只有可独立重建且 hash/归属全通过的行 -> verified。
绝不从当前模板/模型配置猜测历史快照。
"""

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "apps" / "api" / "migrations" / "versions" / "t08_generation_flow.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("t08_generation_flow", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mig = _load_migration()
classify_batch_provenance = _mig.classify_batch_provenance
classify_run_provenance = _mig.classify_run_provenance
_safe_model_config_extra = _mig._safe_model_config_extra


def _batch(**overrides):
    base = dict(
        selected_chunk_ids=["11111111-1111-1111-1111-111111111111"],
        total_chunks=1,
        prompt_template_id="t-1",
        model_config_id="c-1",
        template_version_exists=True,
        template_content_matches_version=True,
        model_config_extra_safe=True,
        has_attributable_run=True,
    )
    base.update(overrides)
    return base


class TestClassifyBatchProvenance:
    def test_verified_when_reconstructible(self):
        assert classify_batch_provenance(**_batch()) == "verified"

    def test_unavailable_missing_selected_chunks(self):
        assert classify_batch_provenance(**_batch(selected_chunk_ids=None)) == "legacy_unavailable"
        assert classify_batch_provenance(**_batch(selected_chunk_ids=[])) == "legacy_unavailable"

    def test_unavailable_missing_config_refs(self):
        assert classify_batch_provenance(**_batch(prompt_template_id=None)) == "legacy_unavailable"
        assert classify_batch_provenance(**_batch(model_config_id=None)) == "legacy_unavailable"

    def test_unavailable_template_version_not_materialized(self):
        assert classify_batch_provenance(**_batch(template_version_exists=False)) == "legacy_unavailable"

    def test_unavailable_model_config_unsafe(self):
        assert classify_batch_provenance(**_batch(model_config_extra_safe=False)) == "legacy_unavailable"

    def test_unavailable_no_attributable_run(self):
        assert classify_batch_provenance(**_batch(has_attributable_run=False)) == "legacy_unavailable"

    def test_invalid_selected_length_conflicts_total(self):
        assert classify_batch_provenance(**_batch(total_chunks=2)) == "invalid"

    def test_invalid_template_content_conflicts_version(self):
        assert classify_batch_provenance(**_batch(template_content_matches_version=False)) == "invalid"


def _run(**overrides):
    base = dict(
        chunk_id="11111111-1111-1111-1111-111111111111",
        input_prompt='[{"role":"system","content":"s"}]',
        batch_provenance="verified",
        chunk_in_batch_selected=True,
    )
    base.update(overrides)
    return base


class TestClassifyRunProvenance:
    def test_verified_when_reconstructible(self):
        assert classify_run_provenance(**_run()) == "verified"

    def test_unavailable_batch_not_verified(self):
        assert classify_run_provenance(**_run(batch_provenance="legacy_unavailable")) == "legacy_unavailable"

    def test_unavailable_missing_input(self):
        assert classify_run_provenance(**_run(chunk_id=None)) == "legacy_unavailable"
        assert classify_run_provenance(**_run(input_prompt=None)) == "legacy_unavailable"
        assert classify_run_provenance(**_run(input_prompt="")) == "legacy_unavailable"

    def test_invalid_chunk_not_in_batch_selection(self):
        assert classify_run_provenance(**_run(chunk_in_batch_selected=False)) == "invalid"


@pytest.mark.parametrize(
    "extra,expected",
    [
        ({"seed": 42}, True),
        ({"response_format": {"type": "json_object"}}, True),
        (None, True),
        ({"api_key": "sk-x"}, False),
        ({"Authorization": "Bearer x"}, False),
        ({"unknown_option": 1}, False),
        ({"credential_ref": "env:X"}, False),
    ],
)
def test_safe_model_config_extra(extra, expected):
    assert _safe_model_config_extra(extra) is expected
