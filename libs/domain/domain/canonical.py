"""Curated 内容/证据/审批的版本化 canonical JSON 与 SHA-256（T09 §4 数据库合同）。

T09 需要三种可在后端、迁移工具与 T10/T11 之间独立重算的确定性 hash：

- ``curated-content-cjson-v1``：CuratedRevision.content 的完整 JSON 快照规范字节。
  - UTF-8 编码（``ensure_ascii=False``，中文等非 ASCII 保留原字符）。
  - Unicode NFC 规范化：先对 key 与字符串值做 NFC，消除组合/分解形式差异。
  - object key 排序（``sort_keys=True``）；数字/布尔/null 按标准 JSON 表示。
  - 紧凑无空白（``separators=(",", ":")``）；``allow_nan=False`` 拒绝非法浮点。
- ``curated-approval-cjson-v1``：审批绑定信息（stable 排序的证据链接 + 被批准
  revision 的 id/content hash + canonicalization version）。
  - 证据链接按 ``(evidence_link_id, chunk_id, start_char, end_char)`` 稳定排序。
  - 引用字符串统一 NFC；offset 使用 Unicode code point（与 span 校验一致）。

所有函数是纯函数，迁移脚本与 T10/T11 可直接 ``from domain.canonical import ...``
复用，保证同一 golden fixture 在任意位置得到相同小写 SHA-256。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

#: 内容规范版本标识（写入 CuratedRevision.canonicalization_version）。
CURATED_CONTENT_CJSON_VERSION = "curated-content-cjson-v1"
#: 审批/证据规范版本标识（写入 ReviewRecord.canonicalization_version）。
CURATED_APPROVAL_CJSON_VERSION = "curated-approval-cjson-v1"


def _normalize(value: Any) -> Any:
    """递归 Unicode NFC 规范化：字符串 key/值均做 NFC，其余类型原样返回。"""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(k)): _normalize(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    return value


def curated_content_cjson(content: dict[str, Any]) -> str:
    """``curated-content-cjson-v1`` 规范 JSON 字符串（供 SHA-256 使用）。

    语义（任务卡 §4）：
    - UTF-8、Unicode NFC；
    - object key 排序；
    - 数字/布尔/null 与紧凑空白规则；
    - ``allow_nan=False``：NaN/Infinity 无法规范表示即抛错（fail closed）。
    """
    return json.dumps(
        _normalize(content),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def curated_content_sha256(content: dict[str, Any]) -> str:
    """CuratedRevision 内容 hash：对 ``curated-content-cjson-v1`` 规范字节计算小写 SHA-256。"""
    return hashlib.sha256(curated_content_cjson(content).encode("utf-8")).hexdigest()


def _evidence_row(link: dict[str, Any]) -> dict[str, Any]:
    """从 EvidenceLink 行提取稳定排序所需的确定性字段。

    行内固定键，缺失字段不允许静默省略（hash 必须覆盖全部绑定坐标）。
    """
    return {
        "evidence_link_id": str(link["id"]),
        "chunk_id": str(link["chunk_id"]),
        "document_id": str(link["document_id"]),
        "start_char": int(link["start_char"]),
        "end_char": int(link["end_char"]),
        "quote_text": unicodedata.normalize("NFC", str(link["quote_text"] or "")),
        "source_pages": _normalize(link.get("source_pages")),
        "heading_path": unicodedata.normalize("NFC", str(link.get("heading_path") or "")),
    }


def curated_evidence_cjson(evidence_links: list[dict[str, Any]]) -> str:
    """``curated-approval-cjson-v1`` 的证据快照部分：稳定排序证据链接的规范 JSON。

    排序键 ``(evidence_link_id, chunk_id, start_char, end_char)``，保证同一组
    证据无论创建顺序如何都产生相同 hash。结果同时作为审计快照
    ``ReviewRecord.evidence_snapshot`` 的来源。
    """
    rows = sorted(
        (_evidence_row(el) for el in evidence_links),
        key=lambda r: (r["evidence_link_id"], r["chunk_id"], r["start_char"], r["end_char"]),
    )
    return json.dumps(
        {"schema_version": 1, "canonicalization_version": CURATED_APPROVAL_CJSON_VERSION, "evidence_links": rows},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def curated_evidence_sha256(evidence_links: list[dict[str, Any]]) -> str:
    """证据快照 hash：稳定排序证据链接的 SHA-256（供审计对照）。"""
    return hashlib.sha256(curated_evidence_cjson(evidence_links).encode("utf-8")).hexdigest()


def curated_approval_cjson(
    *,
    revision_id: Any,
    content_sha256: str,
    evidence_links: list[dict[str, Any]],
) -> str:
    """``curated-approval-cjson-v1`` 完整审批绑定：被批准 revision + 证据链接。

    对稳定排序的 EvidenceLink id、Chunk id、offset、quote/source 与 revision
    id/content hash 计算单一绑定 hash（任务卡 §4.2），保证退审/重批无法静默
    改写历史批准事实。
    """
    payload = {
        "schema_version": 1,
        "canonicalization_version": CURATED_APPROVAL_CJSON_VERSION,
        "revision_id": str(revision_id),
        "content_sha256": str(content_sha256),
        "evidence_links": sorted(
            (_evidence_row(el) for el in evidence_links),
            key=lambda r: (r["evidence_link_id"], r["chunk_id"], r["start_char"], r["end_char"]),
        ),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def curated_approval_sha256(
    *,
    revision_id: Any,
    content_sha256: str,
    evidence_links: list[dict[str, Any]],
) -> str:
    """审批记录 hash：被批准 revision + 证据快照的绑定 SHA-256（存入 evidence_sha256）。"""
    return hashlib.sha256(
        curated_approval_cjson(
            revision_id=revision_id,
            content_sha256=content_sha256,
            evidence_links=evidence_links,
        ).encode("utf-8")
    ).hexdigest()


def validate_hex_sha256(value: str | None) -> bool:
    """校验 64 位小写十六进制 SHA-256（CHECK 约束与 golden fixture 共用）。"""
    if value is None or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)
