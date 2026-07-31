"""测试安全校验：防止集成测试误连开发数据库、Redis 或 MinIO。

任何 destructive fixture（删库、清空 bucket、flushdb）必须先调用
``assert_test_environment_ready()``；不满足隔离条件时直接抛错，绝不触碰
开发数据。
"""

import os
from pathlib import Path

# 允许作为测试数据库的名字。默认严格按任务卡要求为 datasetgen_test。
ALLOWED_TEST_DATABASE_NAMES = {"datasetgen_test"}

# 测试 Redis key 必须带此前缀；测试 MinIO key 必须带此前缀。
TEST_REDIS_KEY_PREFIX = "tests:"
TEST_MINIO_KEY_PREFIX = "tests/"


def _env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def get_test_run_id() -> str:
    """返回本次测试运行的唯一标识（用于 MinIO key 前缀）。"""
    return os.environ.get("TEST_RUN_ID", "local")


def assert_test_environment_ready(*, require_db: bool = True, require_redis: bool = True, require_minio: bool = True) -> None:
    """强制校验当前运行处于隔离测试环境，否则抛出 RuntimeError。

    - 必须显式设置 TESTING=1；
    - 数据库名必须在白名单内（默认为 datasetgen_test）；
    - Redis/MinIO 前缀必须存在且正确（环境变量校验）。
    """
    if not _env_bool("TESTING"):
        raise RuntimeError(
            "TESTING=1 未设置：禁止在非测试模式下执行 destructive fixture。"
        )

    if require_db:
        db_name = os.environ.get("POSTGRES_DB", "")
        if db_name not in ALLOWED_TEST_DATABASE_NAMES:
            raise RuntimeError(
                f"POSTGRES_DB={db_name!r} 不在允许的测试数据库白名单 "
                f"{sorted(ALLOWED_TEST_DATABASE_NAMES)} 内；拒绝执行破坏性操作。"
            )

    if require_redis:
        prefix = os.environ.get("TEST_REDIS_PREFIX", TEST_REDIS_KEY_PREFIX)
        if not prefix or not prefix.endswith(":"):
            raise RuntimeError("TEST_REDIS_PREFIX 必须以 ':' 结尾，否则拒绝执行破坏性操作。")

    if require_minio:
        prefix = os.environ.get("TEST_MINIO_PREFIX", TEST_MINIO_KEY_PREFIX)
        if not prefix or not prefix.endswith("/"):
            raise RuntimeError("TEST_MINIO_PREFIX 必须以 '/' 结尾，否则拒绝执行破坏性操作。")


def is_testing() -> bool:
    return _env_bool("TESTING")


def test_minio_key(prefix: str, *parts: str) -> str:
    """生成带隔离前缀的 MinIO key：tests/{run_id}/..."""
    run_id = get_test_run_id()
    return "/".join([prefix.rstrip("/"), run_id, *parts])


def assert_redis_key_safe(key: str) -> None:
    prefix = os.environ.get("TEST_REDIS_PREFIX", TEST_REDIS_KEY_PREFIX)
    if not key.startswith(prefix):
        raise RuntimeError(f"Redis key {key!r} 未使用隔离前缀 {prefix!r}；拒绝操作。")


def assert_minio_key_safe(key: str) -> None:
    prefix = os.environ.get("TEST_MINIO_PREFIX", TEST_MINIO_KEY_PREFIX)
    if not key.startswith(prefix):
        raise RuntimeError(f"MinIO key {key!r} 未使用隔离前缀 {prefix!r}；拒绝操作。")


def resolve_env_file() -> Path | None:
    """返回仓库根的 .env 测试覆盖文件路径（若存在）。"""
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env.test"
    return env_file if env_file.exists() else None
