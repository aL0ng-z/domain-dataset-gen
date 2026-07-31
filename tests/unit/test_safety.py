"""安全校验模块测试：确保测试隔离保护是强制的，而不是文档约定。"""

import pytest

from tests import safety


def test_requires_testing_flag():
    with pytest.raises(RuntimeError, match="TESTING=1"):
        safety.assert_test_environment_ready()


def test_requires_allowed_database_name(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("POSTGRES_DB", "datasetgen")
    with pytest.raises(RuntimeError, match="白名单"):
        safety.assert_test_environment_ready()


def test_accepts_test_database_name(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("POSTGRES_DB", "datasetgen_test")
    # 不应抛出
    safety.assert_test_environment_ready()


def test_redis_key_must_use_isolated_prefix():
    with pytest.raises(RuntimeError, match="前缀"):
        safety.assert_redis_key_safe("task:123")


def test_redis_key_accepts_test_prefix():
    safety.assert_redis_key_safe("tests:task:123")


def test_minio_key_must_use_isolated_prefix():
    with pytest.raises(RuntimeError, match="前缀"):
        safety.assert_minio_key_safe("documents/foo.pdf")


def test_minio_key_accepts_test_prefix():
    safety.assert_minio_key_safe("tests/local/foo.pdf")


def test_test_minio_key_builds_prefixed_path():
    assert safety.test_minio_key("tests", "p1", "doc.pdf") == "tests/local/p1/doc.pdf"
