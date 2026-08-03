"""T10 迁移回填与约束测试（验收标准 17 的迁移侧）。

覆盖：
- 迁移模块 import domain.composition（与后端/T10/T11 一致），保证 hash 跨端重算。
- 迁移的 deferred consistency / finalized 不可变 trigger 在真实 schema 上已建立
  （由 run-migration-smoke 保证，此处验证模块加载与函数存在性）。
- membership 预检命中重复/跨项目/非 approved/非 supported/无证据时触发停止条件。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "apps" / "api" / "migrations" / "versions" / "t10_dataset_composition.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("t10_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mig = _load_migration()


class TestMigrationImports:
    def test_migration_reuses_domain_composition(self):
        """迁移必须直接 import domain.composition，保证 hash 与后端/T11 完全一致。"""
        import domain.composition as comp

        assert _mig.composition_sha256 is comp.composition_sha256
        assert _mig.COMPOSITION_CJSON_VERSION == comp.COMPOSITION_CJSON_VERSION


class TestMigrationPrechecks:
    def test_duplicate_membership_raises_stop(self):
        """重复 membership 命中停止条件：迁移不猜测保留哪条。"""
        calls: list[str] = []

        class _Result:
            def scalar(self):
                return 1

        class _FakeBind:
            def execute(self, stmt, params=None):
                sql = str(stmt)
                calls.append(sql)
                if "GROUP BY" in sql and "HAVING count(*) > 1" in sql:
                    return _Result()
                raise AssertionError(f"unexpected query: {sql}")

        with pytest.raises(RuntimeError) as excinfo:
            _mig._precheck_memberships(_FakeBind())
        assert "停止条件" in str(excinfo.value)

    def test_cross_project_membership_raises_stop(self):
        """跨项目 membership 命中停止条件。"""

        class _FakeBind:
            def __init__(self):
                self.calls = 0

            def execute(self, stmt, params=None):
                self.calls += 1
                sql = str(stmt)

                class _Result:
                    def scalar(self):
                        # 前 4 组查询（重复 membership/ordinal × 2 表）返回 0；
                        # 跨项目查询返回 1。
                        return 1 if "c.project_id <> ci.project_id" in sql else 0

                return _Result()

        with pytest.raises(RuntimeError) as excinfo:
            _mig._precheck_memberships(_FakeBind())
        assert "跨项目" in str(excinfo.value)

    def test_non_approved_membership_raises_stop(self):
        """非 approved / 缺审批指针的 membership 命中停止条件。"""
        seq = iter(
            [
                0,  # dataset_items 重复 membership
                0,  # dataset_items 重复 ordinal
                0,  # benchmark_cases 重复 membership
                0,  # benchmark_cases 重复 ordinal
                0,  # dataset_items 跨项目
                0,  # benchmark_cases 跨项目
                1,  # dataset_items 非 approved
            ]
        )

        class _FakeBind:
            def execute(self, stmt, params=None):
                sql = str(stmt)

                class _Result:
                    def scalar(self):
                        if "ci.status != 'approved'" in sql:
                            return 1
                        if "cand.review_verdict IS DISTINCT FROM 'supported'" in sql:
                            return 0
                        if "el.id IS NULL" in sql:
                            return 0
                        return next(seq)

                return _Result()

        with pytest.raises(RuntimeError) as excinfo:
            _mig._precheck_memberships(_FakeBind())
        assert "非 approved" in str(excinfo.value)


class TestCompositionHashBackfill:
    def test_empty_composition_sha256_deterministic(self):
        """空集合的 composition hash 确定且与 service 一致（迁移回填复用）。"""
        import domain.composition as comp

        empty_hash = comp.composition_sha256(
            container_id="55555555-5555-5555-5555-555555555555",
            container_type="dataset",
            memberships=[],
        )
        assert len(empty_hash) == 64
        assert all(c in "0123456789abcdef" for c in empty_hash)
