"""Prompt/模型配置冻结快照与 secret 白名单（T08 §4 数据库合同）。

快照字段白名单（任务卡 §4）：
- ``prompt_template_snapshot`` 至少冻结 template/version/task_type/system prompt/
  user template/input schema/output schema；禁止 credential/header/secret。
- ``model_config_snapshot`` 至少冻结 config id/version/provider/model name/
  temperature/max_tokens 及经过字段白名单脱敏的额外参数和端点标识；禁止任何
  credential/header/secret 字段。

hash 使用版本化 canonical JSON 后的 UTF-8 bytes 计算 SHA-256（与 T03
``parsing.snapshot`` 同一方案，但 schema 独立命名避免耦合）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENERATION_SNAPSHOT_SCHEMA_VERSION = 1

#: PromptTemplate 快照字段白名单（顶层固定键，缺失视为无法建立快照）。
PROMPT_SNAPSHOT_KEYS = (
    "template_id",
    "version",
    "task_type",
    "name",
    "system_prompt",
    "user_prompt_template",
    "input_schema",
    "output_schema",
)

#: ModelConfig 快照字段白名单（顶层固定键）。
MODEL_SNAPSHOT_KEYS = (
    "config_id",
    "version",
    "provider",
    "model_name",
    "temperature",
    "max_tokens",
    "base_url",
    "extra_params",
)

#: extra_params 中允许冻结的非秘密键（大小写不敏感、连字符/下划线归一）。
#: 端点/网络标识允许保留 base_url 本身（model_config.base_url），但禁止任何
#: 凭证/header/token 类键。
MODEL_EXTRA_PARAM_ALLOWLIST = frozenset(
    {
        "response_format",
        "stop",
        "seed",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "user",
        "n",
        "timeout_seconds",
        "retries",
    }
)

#: 递归秘密键名（规范化后比较），与 parsing.snapshot 的 SNAPSHOT_SECRET_KEYS 对齐。
SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "access_token",
        "token",
        "secret",
        "credential",
        "authorization",
        "password",
        "auth_scheme",
        "sig",
        "signature",
        "x_api_key",
        "proxy",
        "proxy_authorization",
        "headers",
        "header",
    }
)
_SECRET_NORMALIZED = frozenset(k.replace("-", "").replace("_", "").lower() for k in SECRET_KEY_NAMES)


class SnapshotUnsafeError(Exception):
    """快照包含无法安全分类的秘密字段；调用方不得创建 Batch/Task。"""


class SnapshotBuildError(Exception):
    """快照字段缺失/无法建立 canonical 快照。"""


def _normalized_key(key: str) -> str:
    return key.replace("-", "").replace("_", "").lower()


def canonicalize(value: Any) -> str:
    """固定规范 JSON：ensure_ascii=True、键排序、紧凑无空白。

    与 T03 ``parsing.snapshot.canonicalize`` 语义一致，供快照 hash 使用。
    """
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalize(payload).encode("utf-8")).hexdigest()


def find_secret_field_paths(value: Any, _path: str = "") -> list[str]:
    """递归扫描，返回命中的秘密字段路径（用于 409 GENERATION_SNAPSHOT_UNSAFE context）。"""
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{_path}.{key}" if _path else key
            if _normalized_key(str(key)) in _SECRET_NORMALIZED:
                hits.append(path)
            hits.extend(find_secret_field_paths(item, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(find_secret_field_paths(item, f"{_path}[{idx}]"))
    return hits


def ensure_no_secrets(snapshot: dict[str, Any], *, field_prefix: str) -> None:
    """递归扫描快照，发现秘密字段即抛 :class:`SnapshotUnsafeError`（fail closed）。

    任务卡 §4：``ModelConfig/extra params 无法区分可冻结参数与 secret`` 属于停止
    条件，快照建立前必须能分类；无法分类就停止创建任务。
    """
    hits = find_secret_field_paths(snapshot)
    if hits:
        raise SnapshotUnsafeError(f"快照包含秘密字段: {hits[0]}")


def _require_snapshot_keys(values: dict[str, Any], keys: tuple[str, ...], what: str) -> None:
    """要求快照 dict 覆盖全部白名单键（值为 None 时键仍必须存在）。

    input_schema/output_schema 等可空字段允许为 None（hash 覆盖“无 schema”），
    但键必须显式写入快照，避免字段缺失被静默掩盖。
    """
    missing = [k for k in keys if k not in values]
    if missing:
        raise SnapshotBuildError(f"{what} 快照缺少字段: {', '.join(missing)}")


def _filter_extra_params(extra_params: dict[str, Any] | None) -> dict[str, Any]:
    """按白名单过滤 extra_params，只保留可冻结的非秘密参数。

    未知字段视为无法安全分类 -> 抛 SnapshotUnsafeError（任务卡 §4）。
    """
    cleaned: dict[str, Any] = {}
    for key, value in (extra_params or {}).items():
        nk = _normalized_key(str(key))
        if nk in _SECRET_NORMALIZED:
            raise SnapshotUnsafeError(f"extra_params 包含秘密字段: {key}")
        if nk not in {_normalized_key(k) for k in MODEL_EXTRA_PARAM_ALLOWLIST}:
            # 无法安全分类的字段：fail closed，不得静默丢弃或复制。
            raise SnapshotUnsafeError(f"extra_params 包含无法安全冻结的字段: {key}")
        cleaned[key] = value
    return cleaned


def build_prompt_template_snapshot(template, *, output_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """从 PromptTemplate ORM 构建非秘密快照。"""
    snapshot = {
        "template_id": str(template.id),
        "version": int(template.version),
        "task_type": str(template.task_type),
        "name": str(template.name),
        "system_prompt": str(template.system_prompt),
        "user_prompt_template": str(template.user_prompt_template),
        "input_schema": template.input_schema,
        # 批次创建时传入任务类型基础约束与模板约束合并后的 schema；普通模板快照
        # 调用保持原始配置，避免改变模板版本的持久化语义。
        "output_schema": template.output_schema if output_schema is None else output_schema,
    }
    _require_snapshot_keys(snapshot, PROMPT_SNAPSHOT_KEYS, "PromptTemplate")
    ensure_no_secrets(snapshot, field_prefix="prompt_template_snapshot")
    return snapshot


def build_model_config_snapshot(model_config) -> dict[str, Any]:
    """从 ModelConfig ORM 构建非秘密快照。

    - 保留 provider/model_name/temperature/max_tokens/base_url（端点标识）。
    - extra_params 经白名单过滤；任何秘密/不可分类字段抛 SnapshotUnsafeError。
    - 绝不包含 api_key_encrypted 或 authorization/header/credential。
    """
    extra = _filter_extra_params(model_config.extra_params)
    snapshot = {
        "config_id": str(model_config.id),
        "version": int(model_config.version),
        "provider": str(model_config.provider),
        "model_name": str(model_config.model_name),
        "temperature": model_config.temperature,
        "max_tokens": model_config.max_tokens,
        "base_url": str(model_config.base_url),
        "extra_params": extra,
    }
    _require_snapshot_keys(snapshot, MODEL_SNAPSHOT_KEYS, "ModelConfig")
    ensure_no_secrets(snapshot, field_prefix="model_config_snapshot")
    return snapshot


def prompt_template_snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """PromptTemplate 快照 hash（版本化 canonical JSON SHA-256）。"""
    return _sha256({"snapshot_schema_version": GENERATION_SNAPSHOT_SCHEMA_VERSION, "template": snapshot})


def model_config_snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """ModelConfig 快照 hash（版本化 canonical JSON SHA-256）。"""
    return _sha256({"snapshot_schema_version": GENERATION_SNAPSHOT_SCHEMA_VERSION, "model_config": snapshot})
