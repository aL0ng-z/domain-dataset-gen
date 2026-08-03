"""Dataset/Benchmark composition 的版本化 canonical JSON 与 SHA-256（T10 §4）。

``composition-cjson-v1`` 是 T11 可独立重算的权威 composition hash，语义（任务卡
§4/§11 验收 13）：
- UTF-8、Unicode NFC、object key 排序、紧凑空白、``allow_nan=False``；
- 按 ``(ordinal, membership_id)`` 稳定排序；
- 只包含 schema/version、容器 id/type、membership id、ordinal、CuratedItem id、
  固定 CuratedRevision id/content SHA-256、approval record id/evidence SHA-256；
- 明确排除当前 item 状态、显示名、created_at 等可变字段；空集合也有确定 hash。

T09 的 ``curated-content-cjson-v1`` / ``curated-approval-cjson-v1`` 是 membership
保存的固定 hash 的规范来源；本模块只负责把这些固定值组装为容器级 hash，绝不读
CuratedItem 当前 content。所有函数是纯函数，后端 service、Alembic 迁移与 T11
reader 直接复用。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

#: composition 规范版本标识（写入 Dataset/Benchmark.composition_canonicalization_version）。
COMPOSITION_CJSON_VERSION = "composition-cjson-v1"

#: 容器类型 -> 契约字段名（数据集与基准集共用同一 hash 结构）。
_CONTAINER_KEY = {
    "dataset": "dataset_id",
    "benchmark": "benchmark_id",
}


def _normalize(value: Any) -> Any:
    """递归 Unicode NFC 规范化：字符串 key/值均做 NFC，其余类型原样返回。"""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", str(k)): _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def composition_cjson(
    *,
    container_id: Any,
    container_type: str,
    memberships: list[dict[str, Any]],
) -> str:
    """``composition-cjson-v1`` 规范 JSON 字符串（供 SHA-256 使用）。

    memberships 每项必须是固定字段字典（见 :func:`composition_membership_row`）；
    调用方负责传入加入时固定的 CuratedRevision id/content hash 与 approval record
    id/evidence hash。hash 只依赖这些固定值，退审/重批不会改变已保存的 composition。
    """
    container_key = _CONTAINER_KEY.get(container_type)
    if container_key is None:
        raise ValueError(f"未知容器类型: {container_type!r}")

    # 每行全字段显式展开，缺失字段不允许静默省略（hash 必须覆盖全部固定绑定）。
    rows = []
    for m in memberships:
        row = composition_membership_row(
            membership_id=m["membership_id"],
            ordinal=int(m["ordinal"]),
            curated_item_id=m["curated_item_id"],
            curated_revision_id=m["curated_revision_id"],
            curated_revision_sha256=m["curated_revision_sha256"],
            approval_record_id=m["approval_record_id"],
            approval_evidence_sha256=m["approval_evidence_sha256"],
        )
        rows.append(row)
    rows.sort(key=lambda r: (r["ordinal"], r["membership_id"]))

    payload = _normalize(
        {
            "schema_version": 1,
            "canonicalization_version": COMPOSITION_CJSON_VERSION,
            "container_type": container_type,
            container_key: str(container_id),
            "memberships": rows,
        }
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def composition_membership_row(
    *,
    membership_id: Any,
    ordinal: int,
    curated_item_id: Any,
    curated_revision_id: Any,
    curated_revision_sha256: str,
    approval_record_id: Any,
    approval_evidence_sha256: str,
) -> dict[str, str]:
    """单条 membership 的固定字段（composition hash 的输入单位）。"""
    return {
        "membership_id": str(membership_id),
        "ordinal": str(ordinal),
        "curated_item_id": str(curated_item_id),
        "curated_revision_id": str(curated_revision_id),
        "curated_revision_sha256": str(curated_revision_sha256),
        "approval_record_id": str(approval_record_id),
        "approval_evidence_sha256": str(approval_evidence_sha256),
    }


def composition_sha256(
    *,
    container_id: Any,
    container_type: str,
    memberships: list[dict[str, Any]],
) -> str:
    """composition hash：对 ``composition-cjson-v1`` 规范字节计算小写 SHA-256。"""
    return hashlib.sha256(
        composition_cjson(
            container_id=container_id,
            container_type=container_type,
            memberships=memberships,
        ).encode("utf-8")
    ).hexdigest()
