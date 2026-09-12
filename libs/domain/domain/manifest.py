"""Export manifest 的版本化 canonical JSON 与 SHA-256（T11 §4.4）。

``manifest-cjson-v1`` 是 T11 完整快照清单的规范 hash 语义，与 T09
``curated-content-cjson-v1`` / T10 ``composition-cjson-v1`` 同一套规则：
- UTF-8 编码（``ensure_ascii=False``，中文等非 ASCII 保留原字符）；
- Unicode NFC 规范化（key 与字符串值均做 NFC）；
- object key 排序（``sort_keys=True``）；
- 紧凑无空白（``separators=(",", ":")``）；``allow_nan=False`` 拒绝非法浮点；
- hash 计算**排除 manifest_sha256 自身**，避免自引用。

manifest 是导出时冻结的完整数据快照（不只 ID 列表）。任何缺失链路必须由调用方
显式记录 ``provenance_gap`` 并阻断新导出；本模块只负责把调用方组装好的 manifest
dict 规范化为稳定字节并计算小写 SHA-256。formatter 只读封存快照渲染 payload，
绝不重新读取可变业务表（任务卡 §5.2）。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

#: manifest 规范版本标识（写入 SnapshotManifest.canonicalization_version）。
MANIFEST_CJSON_VERSION = "manifest-cjson-v1"
#: exporter 实现版本（写入 manifest.exporter_version / Export.formatter_version 来源）。
EXPORTER_VERSION = "exporter-v2"
#: manifest JSONB schema 版本（写入 SnapshotManifest.schema_version）。
MANIFEST_SCHEMA_VERSION = 2
#: 排除在 hash 计算之外的字段（自引用排除：manifest 与 seal 各自的 hash）。
_HASH_EXCLUDED_KEYS = frozenset({"manifest_sha256", "seal_sha256"})


def _normalize(value: Any) -> Any:
    """递归 Unicode NFC 规范化：字符串 key/值均做 NFC，其余类型原样返回。"""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", str(k)): _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    return value


def manifest_cjson(manifest: dict[str, Any]) -> str:
    """``manifest-cjson-v1`` 规范 JSON 字符串（供 SHA-256 使用）。

    语义（任务卡 §4.4）：UTF-8、Unicode NFC、object key 排序、数字/布尔/null
    与紧凑空白规则、``allow_nan=False``；hash 计算排除 ``manifest_sha256`` 自身。
    """
    payload = {k: v for k, v in manifest.items() if k not in _HASH_EXCLUDED_KEYS}
    return json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """manifest hash：对 ``manifest-cjson-v1`` 规范字节计算小写 SHA-256。"""
    return hashlib.sha256(manifest_cjson(manifest).encode("utf-8")).hexdigest()


def canonical_bytes_for_seal(seal_payload: dict[str, Any]) -> bytes:
    """``artifact-seal-cjson-v1`` 规范字节（供 DB seal helper 与后端共用）。

    与 manifest-cjson-v1 同规则：UTF-8/NFC/sort_keys/紧凑空白/``allow_nan=False``。
    语义（任务卡 §4.3）：对不含 ``seal_sha256`` 的精确 ``seal_payload`` 做规范化，
    ``seal_sha256 = SHA256(canonical_bytes)``。禁止拼接字符串计算 hash。
    """
    payload = {k: v for k, v in seal_payload.items() if k not in _HASH_EXCLUDED_KEYS}
    return json.dumps(
        _normalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def seal_sha256(seal_payload: dict[str, Any]) -> str:
    """artifact seal hash：对 ``artifact-seal-cjson-v1`` 规范字节计算小写 SHA-256。"""
    return hashlib.sha256(canonical_bytes_for_seal(seal_payload)).hexdigest()
