"""服务端 ParserEndpointRegistry：endpoint_ref 的唯一可信解析源。

endpoint_ref 是不透明稳定 ID，只能解析到服务端 registry；数据库不保存 registry
的真实 URL 或 Token。registry 在启动时验证重复 ref、scheme、origin、凭证绑定和网络
区域，并对外提供安全投影（不含主机/IP/端口/allowlist/网络区域内部细节）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from parsing.egress import Origin, canonicalize_hostname, normalize_url

NetworkZone = Literal["public-remote", "managed-local"]
RedirectPolicy = Literal["deny", "bounded"]


class ArtifactOriginConfig(BaseModel):
    """artifact 允许来源。exact 为精确 origin，suffix 为受控域后缀（*.example.com / example.com）。"""

    usage: str = "any"  # any | upload | download | poll
    exact: str | None = None
    suffix: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ArtifactOriginConfig:
        if bool(self.exact) == bool(self.suffix):
            raise ValueError("artifact origin 必须恰好提供 exact 或 suffix 之一")
        return self


class ParserEndpointConfig(BaseModel):
    endpoint_ref: str
    parser_name: str
    network_zone: NetworkZone = "public-remote"
    #: 规范服务地址。public-remote 为 https URL；managed-local 为精确 http(s) 服务地址。
    base_url: str = ""
    #: 指向环境变量 / Secret Provider 的凭证槽位；managed-local 必须为空。
    credential_ref: str | None = None
    #: 可携带 Authorization 的精确 scheme/host/port 集合。
    credential_origins: list[str] = Field(default_factory=list)
    #: 可上传 PDF / 下载结果的精确 origin 或受控域后缀集合（按用途）。
    artifact_origins: list[ArtifactOriginConfig] = Field(default_factory=list)
    redirect_policy: RedirectPolicy = "deny"
    max_redirects: int = 0
    #: managed-local 允许的精确路径（支持 {task_id} 占位符）。
    allowed_paths: list[str] = Field(default_factory=list)
    #: 固定已验证解析结果（DNS rebinding 防护）；为空时按 zone 规则校验后固定首次解析。
    pinned_ips: list[str] = Field(default_factory=list)
    #: managed-local 内部额外地址（如 vlm_base_url）；admin 配置，不暴露给项目用户。
    additional_urls: dict[str, str] = Field(default_factory=dict)
    display_name: str = ""
    description: str = ""

    @model_validator(mode="after")
    def _validate(self) -> ParserEndpointConfig:
        if not self.endpoint_ref.strip():
            raise ValueError("endpoint_ref 不能为空")
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", self.endpoint_ref):
            raise ValueError(f"endpoint_ref 格式非法: {self.endpoint_ref!r}")
        if self.network_zone not in ("public-remote", "managed-local"):
            raise ValueError("network_zone 必须为 public-remote 或 managed-local")
        if self.network_zone == "managed-local":
            if self.credential_ref or self.credential_origins:
                raise ValueError("managed-local 端点不得携带远程 provider 全局凭证")
            if not self.base_url.startswith(("http://", "https://")):
                raise ValueError("managed-local 端点 base_url 必须以 http(s):// 开头")
        for raw in self.credential_origins:
            normalize_url(raw, scheme="https")
        for artifact in self.artifact_origins:
            if artifact.exact:
                normalize_url(artifact.exact, scheme="https")
            if artifact.suffix:
                canonicalize_hostname(artifact.suffix.lstrip("*."))
        if self.redirect_policy == "bounded" and self.max_redirects < 1:
            raise ValueError("bounded 重定向策略必须设置 max_redirects>=1")
        return self


@dataclass(frozen=True)
class ArtifactOrigin:
    usage: str
    origin: Origin | None = None
    suffix_hosts: frozenset[str] = frozenset()

    def matches(self, host: str, usage: str) -> bool:
        if self.usage not in ("any", usage):
            return False
        if self.origin is not None and host == self.origin.host:
            return True
        return any(host == s or host.endswith(f".{s}") for s in self.suffix_hosts)


@dataclass(frozen=True)
class EndpointDefinition:
    endpoint_ref: str
    parser_name: str
    network_zone: NetworkZone
    base_url: str
    credential_ref: str | None
    credential_origins: tuple[Origin, ...]
    artifact_origins: tuple[ArtifactOrigin, ...]
    redirect_policy: RedirectPolicy
    max_redirects: int
    allowed_paths: tuple[str, ...]
    pinned_ips: tuple[str, ...]
    additional_urls: dict[str, str]
    display_name: str
    description: str

    def policy_snapshot(self) -> dict:
        """序列化为可存 ParseJob 的安全投影（不含 secret、不含内部权限细节以外信息）。"""
        return {
            "endpoint_ref": self.endpoint_ref,
            "parser_name": self.parser_name,
            "network_zone": self.network_zone,
            "base_url": self.base_url,
            "credential_ref": self.credential_ref,
            "credential_origins": [str(o) for o in self.credential_origins],
            "artifact_origins": [
                {
                    "usage": a.usage,
                    "exact": str(a.origin) if a.origin else None,
                    "suffix": sorted(a.suffix_hosts) if a.suffix_hosts else None,
                }
                for a in self.artifact_origins
            ],
            "redirect_policy": self.redirect_policy,
            "max_redirects": self.max_redirects,
            "allowed_paths": list(self.allowed_paths),
            "pinned_ips": list(self.pinned_ips),
            "additional_urls": dict(self.additional_urls),
        }


def _compile_config(config: ParserEndpointConfig) -> EndpointDefinition:
    credential_origins = tuple(
        normalize_url(raw, scheme="https")[1] for raw in config.credential_origins
    )
    artifact_origins: list[ArtifactOrigin] = []
    for artifact in config.artifact_origins:
        if artifact.exact:
            _, origin = normalize_url(artifact.exact, scheme="https")
            artifact_origins.append(ArtifactOrigin(usage=artifact.usage, origin=origin))
        else:
            suffix_hosts = frozenset(
                canonicalize_hostname(part)
                for part in artifact.suffix.lstrip("*.").split(",")
                if part.strip()
            )
            artifact_origins.append(ArtifactOrigin(usage=artifact.usage, suffix_hosts=suffix_hosts))
    return EndpointDefinition(
        endpoint_ref=config.endpoint_ref,
        parser_name=config.parser_name,
        network_zone=config.network_zone,
        base_url=config.base_url.rstrip("/"),
        credential_ref=config.credential_ref,
        credential_origins=tuple(credential_origins),
        artifact_origins=tuple(artifact_origins),
        redirect_policy=config.redirect_policy,
        max_redirects=config.max_redirects,
        allowed_paths=tuple(config.allowed_paths),
        pinned_ips=tuple(config.pinned_ips),
        additional_urls=dict(config.additional_urls),
        display_name=config.display_name or config.endpoint_ref,
        description=config.description,
    )


class ParserEndpointRegistry:
    """启动时验证的只读 registry；提供端点解析与安全投影。"""

    def __init__(self, configs: list[ParserEndpointConfig]):
        definitions = [_compile_config(c) for c in configs]
        self._by_ref: dict[str, EndpointDefinition] = {}
        self._by_parser: dict[str, EndpointDefinition] = {}
        for definition in definitions:
            if definition.endpoint_ref in self._by_ref:
                raise ValueError(f"endpoint_ref 重复: {definition.endpoint_ref}")
            if definition.parser_name in self._by_parser:
                raise ValueError(f"parser_name 绑定到多个端点: {definition.parser_name}")
            self._by_ref[definition.endpoint_ref] = definition
            self._by_parser[definition.parser_name] = definition

    @classmethod
    def from_json(cls, payload: str | None) -> ParserEndpointRegistry:
        if not payload:
            return cls([])
        try:
            configs = [ParserEndpointConfig.model_validate(item) for item in json.loads(payload)]
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"PARSER_ENDPOINT_REGISTRY 配置非法: {exc}") from exc
        return cls(configs)

    def get(self, endpoint_ref: str) -> EndpointDefinition | None:
        return self._by_ref.get(endpoint_ref)

    def get_for_parser(self, parser_name: str) -> EndpointDefinition | None:
        return self._by_parser.get(parser_name)

    def require(self, endpoint_ref: str) -> EndpointDefinition:
        definition = self.get(endpoint_ref)
        if definition is None:
            raise KeyError(f"未知 endpoint_ref: {endpoint_ref}")
        return definition

    def requires_credential(self, definition: EndpointDefinition) -> bool:
        return definition.credential_ref is not None and bool(definition.credential_origins)

    def list_for_ui(self) -> list[dict]:
        """只读安全投影：不返回主机/IP/端口/allowlist 或网络区域内部细节。"""
        from app.config import settings

        def credential_ready(definition: EndpointDefinition) -> bool:
            if not definition.credential_ref:
                return True
            name = definition.credential_ref.split(":", 1)[-1].lower()
            value = getattr(settings, name, None)
            raw = value.get_secret_value() if hasattr(value, "get_secret_value") else value
            return bool(raw and str(raw).strip())

        return [
            {
                "endpoint_ref": d.endpoint_ref,
                "display_name": d.display_name,
                "parser_name": d.parser_name,
                "credential_configured": credential_ready(d),
            }
            for d in sorted(self._by_ref.values(), key=lambda d: d.endpoint_ref)
        ]


def build_registry(configs: list[ParserEndpointConfig] | None = None) -> ParserEndpointRegistry:
    from app.config import settings

    if configs is None:
        configs = settings.parser_endpoint_registry
    if isinstance(configs, str):
        return ParserEndpointRegistry.from_json(configs)
    return ParserEndpointRegistry(configs)


# 供 fastapi Depends 使用的单例（启动时构建一次）。
_registry: ParserEndpointRegistry | None = None


def get_registry() -> ParserEndpointRegistry:
    global _registry
    if _registry is None:
        _registry = build_registry()
    return _registry


def reset_registry() -> None:
    """仅供测试重置。"""
    global _registry
    _registry = None
