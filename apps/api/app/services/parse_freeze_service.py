"""ParseJob 创建时原子冻结 profile/policy 快照（T03 核心链路）。

职责：
- 读取有效 ParserProfile + registry policy，清除秘密、规范化并计算 SHA-256；
- 在同一事务内一次性写入 ParseJob 的快照/hash/ref/version 字段；
- 解析器安全上下文（_security）在此构造并注入 worker 使用的 options；
- 对旧网络字段 profile 或缺失 registry 端点执行 fail-closed 拒绝。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import ParserProfile
from app.models.parse import ParseJob
from app.security.registry import get_registry
from app.security.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    build_profile_snapshot,
    policy_snapshot_sha256,
    profile_snapshot_sha256,
    scan_for_secrets,
    validate_parser_options,
)
from parsing.snapshot import LOCAL_PARSERS


class ParseFreezeError(ValueError):
    """冻结失败：registry 缺端点、凭证未配置、旧 profile 未迁移或快照含秘密。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _registry():
    return get_registry()


def freeze_profile_policy(
    profile: ParserProfile,
    *,
    require_credential_ready: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """构造 profile/policy 快照与安全上下文（纯函数，无 DB 访问）。

    返回 (profile_snapshot, policy_snapshot, security_context)。
    不合法时抛 ParseFreezeError（fail closed）。
    """
    parser_name = profile.parser_name
    raw_options = profile.parser_options or {}

    # 1. 白名单收紧 + 禁止字段递归拒绝（即使存量数据也 fail closed）。
    try:
        functional_options = validate_parser_options(parser_name, raw_options)
    except ValueError as exc:
        raise ParseFreezeError("unsafe_parser_option", str(exc)) from exc

    # 2. 本地解析器（无需 endpoint）直接构造 profile 快照（无 policy）。
    if parser_name in LOCAL_PARSERS:
        profile_snapshot = build_profile_snapshot(
            profile_id=str(profile.id),
            version=profile.version,
            parser_name=parser_name,
            endpoint_ref=None,
            functional_options=functional_options,
        )
        policy_snapshot: dict[str, Any] = {
            "endpoint_ref": "local",
            "parser_name": parser_name,
            "network_zone": "managed-local",
            "credential_ref": None,
        }
        security: dict[str, Any] = {}
        return profile_snapshot, policy_snapshot, security

    # 3. 远端 / 本地服务解析器：endpoint_ref 必须解析到 registry。
    endpoint_ref = (functional_options or {}).get("endpoint_ref")
    if not endpoint_ref:
        raise ParseFreezeError("invalid_parser_endpoint", "解析器配置缺少 endpoint_ref")
    definition = _registry().get(str(endpoint_ref))
    if definition is None:
        raise ParseFreezeError("invalid_parser_endpoint", "endpoint_ref 未在服务端 registry 注册")
    if definition.parser_name != parser_name:
        raise ParseFreezeError("invalid_parser_endpoint", "endpoint_ref 与解析器类型不匹配")

    # 4. 凭证就绪性检查（触发解析前 409；创建/更新 profile 不做此检查）。
    if require_credential_ready and definition.credential_ref:
        from app.config import settings

        token = getattr(settings, _credential_setting_name(definition.credential_ref), None)
        if token is None or not (token.get_secret_value() if hasattr(token, "get_secret_value") else token):
            raise ParseFreezeError("credential_not_configured", "端点凭证未配置，无法触发解析")

    # 5. 构造 policy 快照（执行所需规范 origin/allowlist/网络区/重定向规则，无秘密）。
    policy_snapshot = definition.policy_snapshot()

    # 6. 构造 profile 快照（endpoint_ref + 功能参数，无网络字段）。
    profile_snapshot = build_profile_snapshot(
        profile_id=str(profile.id),
        version=profile.version,
        parser_name=parser_name,
        endpoint_ref=str(endpoint_ref),
        functional_options={k: v for k, v in (functional_options or {}).items() if k != "endpoint_ref"},
    )

    # 7. 快照递归扫描：绝不允许出现 secret/Token/Authorization/PDF/预签名 URL。
    hits = scan_for_secrets(profile_snapshot)
    if hits:
        raise ParseFreezeError("snapshot_contains_secret", f"profile 快照含秘密特征字段: {hits[0]}")
    hits = scan_for_secrets(policy_snapshot)
    if hits:
        raise ParseFreezeError("snapshot_contains_secret", f"policy 快照含秘密特征字段: {hits[0]}")

    # 8. 安全上下文（worker 注入 parser options；不含真实 token）。
    security = {
        "endpoint_ref": definition.endpoint_ref,
        "base_url": definition.base_url,
        "network_zone": definition.network_zone,
        "credential_origins": [str(o) for o in definition.credential_origins],
        "credential_ref": definition.credential_ref,
    }
    if definition.network_zone == "managed-local":
        security["managed_local"] = {
            "port": _managed_local_port(definition.base_url),
            "pinned_ips": list(definition.pinned_ips) or _pinned_localhost(definition.base_url),
            "vlm_http_url": definition.additional_urls.get("vlm_http_url"),
        }

    return profile_snapshot, policy_snapshot, security


def _credential_setting_name(credential_ref: str) -> str:
    """credential_ref 如 'env:MINERU_API_TOKEN' -> 'mineru_api_token'。"""
    ref = credential_ref.split(":", 1)[-1]
    return ref.lower()


def _managed_local_port(base_url: str) -> int | None:
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    try:
        return parts.port or (80 if parts.scheme == "http" else 443)
    except ValueError:
        return None


def _pinned_localhost(base_url: str) -> list[str]:
    """managed-local 默认固定 127.0.0.1（本地部署服务）。"""
    from urllib.parse import urlsplit

    host = (urlsplit(base_url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1"}:
        return ["127.0.0.1"]
    return []


async def freeze_parse_job(
    db: AsyncSession,
    *,
    document_id: uuid.UUID,
    profile: ParserProfile,
    require_credential_ready: bool = False,
) -> ParseJob:
    """在同一事务内创建带完整快照的 ParseJob（含 hash/ref/version/frozen_at）。"""
    profile_snapshot, policy_snapshot, security = freeze_profile_policy(
        profile, require_credential_ready=require_credential_ready
    )
    schema_version = SNAPSHOT_SCHEMA_VERSION
    policy_ref = policy_snapshot.get("endpoint_ref", "local")
    policy_version = str(profile.version)

    job = ParseJob(
        document_id=document_id,
        parser_profile_id=profile.id,
        status="queued",
        snapshot_schema_version=schema_version,
        parser_profile_snapshot=profile_snapshot,
        parser_profile_sha256=profile_snapshot_sha256(profile_snapshot, schema_version),
        endpoint_policy_snapshot=policy_snapshot,
        endpoint_policy_ref=policy_ref,
        endpoint_policy_version=policy_version,
        endpoint_policy_sha256=policy_snapshot_sha256(policy_snapshot, schema_version),
        frozen_at=datetime.now(UTC),
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return job


def build_worker_options(
    parse_job: ParseJob,
    *,
    parser_name: str,
    stored_options: dict[str, Any] | None,
    security: dict[str, Any],
    api_tokens: dict[str, Any],
) -> dict[str, Any]:
    """构造 worker 实际使用的 parser options。

    - 功能参数来自冻结 profile 快照（不是当前 profile）；
    - 全局 Token 仅在此刻、发送到绑定 credential origin 前即时注入；
    - 注入的 _security 携带 credential origins 供 transport 校验。
    """
    options: dict[str, Any] = dict(stored_options or {})
    options["_security"] = security

    if security.get("network_zone") == "public-remote":
        token = api_tokens.get(parser_name)
        raw_token = token.get_secret_value() if hasattr(token, "get_secret_value") else token
        if security.get("credential_ref") and raw_token and str(raw_token).strip():
            options["api_key"] = str(raw_token).strip()
    return options
