"""统一构造对象存储 key。

生产默认不加前缀，保持既有对象坐标合同；测试通过 ``MINIO_KEY_PREFIX`` 把所有
新写入限制在 ``tests/{run_id}/``，便于按精确 version 安全清理。
"""

from __future__ import annotations

from app.config import settings


def build_storage_key(*parts: object) -> str:
    """连接非空 path segment，并在配置存在时添加统一前缀。"""
    segments: list[str] = []
    prefix = settings.minio_key_prefix.strip("/")
    if prefix:
        segments.append(prefix)
    for part in parts:
        value = str(part).strip("/")
        if value:
            segments.append(value)
    return "/".join(segments)
