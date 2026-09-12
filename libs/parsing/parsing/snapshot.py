"""ParseJob 冻结快照：规范化 JSON / SHA-256 / 递归脱敏 / 秘密扫描。

位于 libs/parsing，与解析器共享；apps/api 通过 ``app.security`` 复用同一实现。

两个 SHA-256 都基于带 snapshot_schema_version 的规范 JSON 字节计算；键排序、
Unicode、数字与空值规范必须固定并由 canonicalize 函数生成，hash 字段存小写十六进制。
快照严禁包含真实 secret、Token、Authorization、PDF 字节、provider 响应或预签名 URL；
这些值也不得参与 hash。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SNAPSHOT_SCHEMA_VERSION = 1

# 纯本地解析器不需要 HTTP endpoint；CRUD、冻结和 seed 共用。
LOCAL_PARSERS = frozenset({"pymupdf4llm", "mock", "mineru_local"})

#: 禁止出现在 parser_options 的网络/秘密字段名（递归、大小写不敏感、连字符/下划线归一）。
FORBIDDEN_OPTION_KEYS = frozenset(
    {
        "base_url",
        "upload_url",
        "results_url",
        "vlm_base_url",
        "server_url",
        "proxy",
        "headers",
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
    }
)

#: 规范化后与禁止字段等价的别名（去掉连字符/下划线）。
_FORBIDDEN_NORMALIZED = frozenset(key.replace("-", "").replace("_", "").lower() for key in FORBIDDEN_OPTION_KEYS)

#: 快照扫描的秘密键名（不含 base_url/upload_url 等网络字段——那些是 profile options
#: 的禁止项，但 policy 快照中 registry 派生的规范 URL 是合法执行信息）。
SNAPSHOT_SECRET_KEYS = frozenset(
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
    }
)
_SNAPSHOT_SECRET_NORMALIZED = frozenset(k.replace("-", "").replace("_", "").lower() for k in SNAPSHOT_SECRET_KEYS)

#: 快照中禁止出现的值特征：预签名 URL query、Authorization 头值等。
_VALUE_PATTERNS = (
    re.compile(r"X-Amz-Signature=", re.IGNORECASE),
    re.compile(r"X-Goog-Signature=", re.IGNORECASE),
    re.compile(r"Signature=", re.IGNORECASE),
    re.compile(r"(?i)^Bearer\s+[A-Za-z0-9._-]{16,}"),
)

#: 可放进 parser_options 的 parser_name -> 字段白名单。
ALLOWED_OPTION_FIELDS: dict[str, frozenset[str]] = {
    "pymupdf4llm": frozenset(),
    "mock": frozenset(),
    "mineru": frozenset(
        {
            "endpoint_ref",
            "model_version",
            "enable_formula",
            "enable_table",
            "language",
            "timeout_seconds",
            "poll_interval_seconds",
            "max_wait_seconds",
        }
    ),
    "paddleocr": frozenset(
        {
            "endpoint_ref",
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_textline_orientation",
            "visualize",
            "timeout_seconds",
        }
    ),
    "mineru_local": frozenset(
        {
            "model_path",
            "device_map",
            "render_dpi",
            "max_pages",
            "image_analysis",
        }
    ),
    "mineru_local_service": frozenset(
        {
            "endpoint_ref",
            "backend",
            "language",
            "parse_method",
            "formula_enable",
            "table_enable",
            "image_analysis",
            "timeout_seconds",
            "poll_interval_seconds",
            "max_wait_seconds",
            "auto_start",
            "startup_timeout_seconds",
        }
    ),
    "paddleocr_local_service": frozenset(
        {
            "endpoint_ref",
            "use_doc_orientation_classify",
            "use_doc_unwarping",
            "use_textline_orientation",
            "use_layout_detection",
            "whole_page_smoke",
            "visualize",
            "max_new_tokens",
            "timeout_seconds",
            "parse_timeout_seconds",
            "auto_start",
            "startup_timeout_seconds",
        }
    ),
}


def normalized_key(key: str) -> str:
    return key.replace("-", "").replace("_", "").lower()


def is_forbidden_option_key(key: str) -> bool:
    return normalized_key(key) in _FORBIDDEN_NORMALIZED


def allowed_fields_for(parser_name: str) -> frozenset[str]:
    return ALLOWED_OPTION_FIELDS.get(parser_name, frozenset())


def find_forbidden_keys(options: dict[str, Any], _path: str = "") -> list[str]:
    """递归扫描 parser_options，返回所有禁止字段的路径；禁止字段检查必须递归。"""
    hits: list[str] = []
    if not isinstance(options, dict):
        return hits
    for key, value in options.items():
        path = f"{_path}.{key}" if _path else key
        if is_forbidden_option_key(key):
            hits.append(path)
        elif isinstance(value, dict):
            hits.extend(find_forbidden_keys(value, path))
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    hits.extend(find_forbidden_keys(item, f"{path}[{idx}]"))
    return hits


def validate_parser_options(parser_name: str, options: dict[str, Any] | None) -> dict[str, Any] | None:
    """按 parser_name 白名单校验 parser_options；拒绝未知字段与禁止字段。

    返回清理后的副本（保持键序），失败抛 ValueError（稳定错误信息）。
    """
    if options is None:
        return None
    forbidden = find_forbidden_keys(options)
    if forbidden:
        raise ValueError(f"unsafe_parser_option: 发现禁用字段 {forbidden[0]}")
    allowed = allowed_fields_for(parser_name)
    cleaned: dict[str, Any] = {}
    for key, value in options.items():
        if key not in allowed:
            raise ValueError(f"unsafe_parser_option: 未知字段 {key}")
        cleaned[key] = value
    return cleaned


def canonicalize(value: Any) -> str:
    """固定规范 JSON 字节：ensure_ascii=True、键排序、紧凑无空白。

    数字/布尔/空值保持原样（bool 是 int 子类，需先判 bool），字符串不折叠空白。
    """
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def profile_snapshot_sha256(snapshot: dict[str, Any], schema_version: int = SNAPSHOT_SCHEMA_VERSION) -> str:
    payload = {"snapshot_schema_version": schema_version, "profile": snapshot}
    return hashlib.sha256(canonicalize(payload).encode("utf-8")).hexdigest()


def policy_snapshot_sha256(snapshot: dict[str, Any], schema_version: int = SNAPSHOT_SCHEMA_VERSION) -> str:
    payload = {"snapshot_schema_version": schema_version, "policy": snapshot}
    return hashlib.sha256(canonicalize(payload).encode("utf-8")).hexdigest()


def build_profile_snapshot(
    *,
    profile_id: str,
    version: int,
    parser_name: str,
    endpoint_ref: str | None,
    functional_options: dict[str, Any] | None,
) -> dict[str, Any]:
    """构造可审计 profile 快照（无秘密）。endpoint_ref 仅对远端/本地服务解析器必填。"""
    return {
        "profile_id": profile_id,
        "version": version,
        "parser_name": parser_name,
        "endpoint_ref": endpoint_ref,
        "options": functional_options or {},
    }


def scan_for_secrets(value: Any, _path: str = "") -> list[str]:
    """递归扫描快照，返回命中的秘密特征路径（Token/Authorization/预签名 query/PDF 标记）。"""
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{_path}.{key}" if _path else key
            if normalized_key(key) in _SNAPSHOT_SECRET_NORMALIZED:
                hits.append(path)
            hits.extend(scan_for_secrets(item, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            hits.extend(scan_for_secrets(item, f"{_path}[{idx}]"))
    elif isinstance(value, str):
        if len(value) > 0:
            for pattern in _VALUE_PATTERNS:
                if pattern.search(value):
                    hits.append(_path or "<root>")
                    break
        if len(value) > 4096:
            hits.append(_path or "<root>")
    return hits


def redact(value: Any) -> Any:
    """递归脱敏：禁止字段与特征值替换为 [REDACTED]，用于日志/API 响应。"""
    if isinstance(value, dict):
        return {k: "[REDACTED]" if is_forbidden_option_key(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        if len(value) > 4096:
            return "[REDACTED]"
        if any(pattern.search(value) for pattern in _VALUE_PATTERNS):
            return "[REDACTED]"
        return value
    return value


def redact_url(url: str) -> str:
    """脱敏 URL query 中的签名参数，避免把 signed query 写入日志/错误。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    cleaned = [
        (key, "[REDACTED]" if normalized_key(key) in _FORBIDDEN_NORMALIZED or "signature" in normalized_key(key) else value)
        for key, value in query
    ]
    # urlencode 默认会编码方括号；用 safe 保留它们，避免 [REDACTED] 变成 %5B...%5D。
    encoded_query = urlencode(cleaned, safe="[]")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, encoded_query, parts.fragment))


def mask_authorization_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: ("[REDACTED]" if key.lower() in {"authorization", "proxy-authorization", "x-api-key"} else value)
        for key, value in headers.items()
    }
