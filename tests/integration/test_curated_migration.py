"""T09 迁移回填与约束测试（验收标准 11/10 的迁移侧）。

覆盖：
- 迁移模块的 _precheck_review_status 命中非默认 review_status 时触发停止条件。
- 迁移模块 import domain.canonical（与后端/T10 一致），保证 hash 跨端重算。
- 迁移的不可变/deferred trigger 在真实 schema 上已建立（由 run-migration-smoke 保证）。
- 无 evidence 的历史 CuratedItem 在迁移后保持 draft（诚实回填策略的纯逻辑）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "apps" / "api" / "migrations" / "versions" / "t09_curated_evidence_approval.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("t09_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mig = _load_migration()


class TestMigrationImports:
    def test_migration_reuses_domain_canonical(self):
        """迁移必须直接 import domain.canonical，保证 hash 与后端/T10 完全一致。"""
        import domain.canonical as canon

        assert _mig.curated_content_sha256 is canon.curated_content_sha256
        assert _mig.CURATED_CONTENT_CJSON_VERSION == canon.CURATED_CONTENT_CJSON_VERSION
        assert _mig.CURATED_APPROVAL_CJSON_VERSION == canon.CURATED_APPROVAL_CJSON_VERSION


class TestMigrationPrechecks:
    def test_review_status_divergent_raises_stop(self):
        """非默认 review_status 命中停止条件：迁移不猜测历史审核结论。"""
        class _Result:
            def scalar(self):
                # 任意候选的 review_status != 'pending' -> 返回 1。
                return 1

        class _FakeBind:
            def execute(self, stmt, params=None):
                assert "review_status IS DISTINCT FROM 'pending'" in str(stmt)
                return _Result()

        with pytest.raises(RuntimeError) as excinfo:
            _mig._precheck_review_status(_FakeBind())
        assert "命中停止条件" in str(excinfo.value)


class TestMigrationBackfillRows:
    def test_backfill_uses_mapping_rows(self):
        """SQLAlchemy 2.x Row 需要 mappings()，不能用字符串下标访问 tuple。"""
        updates: list[dict] = []

        class _Rows:
            def mappings(self):
                return self

            def all(self):
                return [{"id": "r1", "curated_item_id": "item1", "content": {"question": "Q"}}]

        class _FakeBind:
            def execute(self, stmt, params=None):
                if "SELECT id, curated_item_id, content" in str(stmt):
                    return _Rows()
                updates.append(params)
                return None

        assert _mig._backfill_curated_revisions(_FakeBind()) == 1
        assert updates[0]["rid"] == "r1"

    def test_nfc_quote_coordinates_remain_in_original_text(self):
        """NFC 后 X 位于 index=2，但原文 e + combining acute 后 X 位于 index=3。"""
        assert _mig._find_quote_span_in_original("e\u0301 X", "X") == (3, 4)


class TestDemotePolicy:
    def test_unverifiable_items_demoted(self):
        """无审批记录的 approved 历史条目退回 draft（诚实回填，绝不自动设 approved）。"""
        calls: list[str] = []

        class _FakeBind:
            def execute(self, stmt, params=None):
                sql = str(stmt)
                if "SELECT id FROM curated_items" in sql:
                    class _Result:
                        def mappings(self):
                            return self

                        def all(self):
                            return [{"id": "11111111-1111-1111-1111-111111111111"}]

                    return _Result()
                calls.append(sql)
                return None

        n = _mig._demote_approved_items(_FakeBind())
        assert n == 1
        assert any("SET status = 'draft'" in sql for sql in calls)
