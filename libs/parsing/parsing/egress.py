"""解析器出站网络策略核心：URL 规范化、IP 分类、origin 匹配、重定向与凭证转发。

本模块位于 libs/parsing，使所有解析器（remote / managed-local）共享同一安全边界，
apps/api 的 app.security 通过 ``from parsing.egress import *`` 复用同一实现。

设计要点（对齐 T03 §3/§5.3/§9）：
- public-remote 仅允许 HTTPS；禁止 userinfo/fragment/非注册端口/非规范 hostname。
- hostname 经 IDNA/大小写/尾点规范化后，与精确 origin 或边界正确的受控域后缀匹配。
- 解析得到的全部 A/AAAA 地址都必须为允许的公网地址；任一地址危险即整体拒绝。
- credential request 默认禁止自动重定向；有界重定向每跳重新执行本策略。
- 敏感头（Authorization/Token）只允许转发到被允许的 credential origin。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal

#: 可携带凭证的敏感请求头。其余请求头（Content-Type、Accept 等）视为普通头。
SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "x-api-key"})

#: 云元数据地址（169.254.169.254 / fd00:ec2::254 等）。不在常规私网/链路本地集合内，
#: 单独列出以确保 fail-closed。
CLOUD_METADATA_NETS = (
    ipaddress.ip_network("169.254.169.254/32"),
    ipaddress.ip_network("169.254.169.123/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("100.100.100.203/32"),
)

_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class EgressSecurityError(ValueError):
    """出站策略拒绝，携带稳定、无秘密的安全错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class UrlSafetyError(EgressSecurityError):
    """URL 规范化/允许集合不匹配。"""


class IpSafetyError(EgressSecurityError):
    """IP 地址不在允许集合。"""


class RedirectSafetyError(EgressSecurityError):
    """重定向被禁止或下一跳不安全。"""


@dataclass(frozen=True)
class Origin:
    """规范化 origin：scheme + host（IDNA 小写，无尾点）+ 端口。"""

    scheme: str
    host: str
    port: int | None
    #: 受控域后缀集合（边界正确匹配：".example.com" 或 "example.com"）。
    #: 仅当 allow_subdomains 时用于匹配；精确 origin 不在此列。
    controlled_suffixes: frozenset[str] = frozenset()
    allow_subdomains: bool = False

    @property
    def default_port(self) -> int:
        return 443 if self.scheme == "https" else 80

    @property
    def normalized_port(self) -> int | None:
        if self.port is None or self.port == self.default_port:
            return None
        return self.port

    def __str__(self) -> str:
        port = f":{self.normalized_port}" if self.normalized_port else ""
        return f"{self.scheme}://{self.host}{port}"

    def matches_host(self, host: str) -> bool:
        """host 必须是规范化 hostname（无尾点、小写）。匹配精确 host 或受控域后缀。"""
        if host == self.host:
            return True
        if not self.allow_subdomains:
            return False
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in self.controlled_suffixes)


def canonicalize_hostname(host: str) -> str:
    """IDNA + 小写 + 去掉尾点；非法 hostname 抛 UrlSafetyError。"""
    raw = host.strip().rstrip(".").lower()
    if not raw:
        raise UrlSafetyError("unsafe_parser_url", "URL 缺少主机名")
    if re.search(r"[\s/\\@:]", raw):
        raise UrlSafetyError("unsafe_parser_url", "URL 主机名包含非法字符")
    try:
        ascii_host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlSafetyError("unsafe_parser_url", "URL 主机名 IDNA 规范化失败") from exc
    if not re.match(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$", ascii_host):
        raise UrlSafetyError("unsafe_parser_url", "URL 主机名格式不合法")
    return ascii_host


def normalize_url(
    url: str,
    *,
    scheme: str | tuple[str, ...] = "https",
    allowed_port: int | None = None,
    allow_private_host: bool = False,
) -> tuple[str, Origin]:
    """规范化并校验 URL；返回 (规范化 URL, Origin)。任何非法项都抛 UrlSafetyError。

    - public-remote（默认）：仅 https；禁止 userinfo/fragment；端口必须匹配 registry。
    - managed-local：允许 http/https（scheme 传入元组），host 必须来自 registry 的精确主机。
    """
    from urllib.parse import urlsplit, urlunsplit

    allowed_schemes = (scheme,) if isinstance(scheme, str) else tuple(scheme)
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UrlSafetyError("unsafe_parser_url", "URL 无法解析") from exc

    scheme_used = parts.scheme.lower()
    if scheme_used not in allowed_schemes:
        raise UrlSafetyError("unsafe_parser_url", "不允许的 URL scheme")
    if parts.username is not None or parts.password is not None:
        raise UrlSafetyError("unsafe_parser_url", "URL 禁止包含 userinfo")
    if parts.fragment:
        raise UrlSafetyError("unsafe_parser_url", "URL 禁止包含 fragment")

    host = canonicalize_hostname(parts.hostname or "")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlSafetyError("unsafe_parser_url", "URL 端口格式非法") from exc
    if allowed_port is not None:
        if port not in (None, allowed_port):
            raise UrlSafetyError("unsafe_parser_url", "URL 端口不在允许集合内")
    elif port is not None and port not in (443 if scheme_used == "https" else 80,):
        # public-remote 默认只允许默认端口；managed-local 通过 allowed_port 显式放行。
        raise UrlSafetyError("unsafe_parser_url", "URL 端口不在允许集合内")
    if port is not None and not (1 <= port <= 65535):
        raise UrlSafetyError("unsafe_parser_url", "URL 端口超出范围")

    origin = Origin(scheme=scheme_used, host=host, port=port)
    normalized_host = host + (f":{port}" if port not in (None, origin.default_port) else "")
    path = parts.path or "/"
    query = parts.query
    normalized = urlunsplit((scheme_used, normalized_host, path, query, ""))
    return normalized, origin


def classify_ip(
    address: str,
) -> Literal["public", "private", "loopback", "link_local", "multicast", "reserved", "unspecified", "cloud_metadata", "invalid"]:
    """对单个 IP 字符串分类。IPv4 支持十进制/八进制等混淆写法。"""
    raw = address.strip()
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        try:
            ip = _parse_mixed_ip(raw)
        except ValueError:
            return "invalid"

    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if any(ip in net for net in CLOUD_METADATA_NETS):
        return "cloud_metadata"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved and not (ip.is_private or ip.is_link_local):
        return "reserved"
    if ip.is_link_local:
        return "link_local"
    if ip.is_private or any(ip in net for net in _PRIVATE_NETS):
        return "private"
    if not ip.is_global:
        return "reserved"
    return "public"


def _parse_mixed_ip(raw: str) -> ipaddress.IPv4Address:
    """解析混淆 IPv4：纯整数、八进制、混合（127.1、0177.0.0.1、0x7f.0.0.1）。

    少于 4 段时末段吸收剩余字节：127.1 -> 127.0.0.1；前导零按八进制。
    """
    parts = raw.split(".")
    if len(parts) > 4:
        raise ValueError(raw)
    if len(parts) == 1:
        value = int(raw, 0)
        if value < 0 or value > 0xFFFFFFFF:
            raise ValueError(raw)
        return ipaddress.IPv4Address(value)

    # 每个 part 的位宽：最后一段额外吸收 4 - len(parts) 字节。
    bits = [8] * len(parts)
    bits[-1] = (4 - len(parts) + 1) * 8
    value = 0
    for part, width in zip(parts, bits, strict=True):
        if part == "":
            raise ValueError(raw)
        if width == 8 and _looks_octal(part) and int(part, 8) > 255:
            raise ValueError(raw)
        parsed = int(part, 8) if _looks_octal(part) else int(part, 16 if part.lower().startswith("0x") else 10)
        if parsed < 0 or parsed >= (1 << width):
            raise ValueError(raw)
        value = (value << width) | parsed
    return ipaddress.IPv4Address(value)


def _looks_octal(part: str) -> bool:
    return len(part) > 1 and part[0] == "0" and part.isdigit()


def validate_all_ips(addresses: list[str]) -> list[str]:
    """校验一批 IP；任一地址危险即整体拒绝。返回规范化后的地址列表。"""
    if not addresses:
        raise IpSafetyError("dns_failed", "无法解析任何地址")
    cleaned: list[str] = []
    for address in addresses:
        raw = address.strip().strip("[]")
        kind = classify_ip(raw)
        if kind == "invalid":
            raise IpSafetyError("unsafe_parser_ip", "地址格式非法")
        if kind != "public":
            raise IpSafetyError("unsafe_parser_ip", f"目标 IP 不在允许集合（{kind}）")
        cleaned.append(str(ipaddress.ip_address(raw)))
    return cleaned


def split_headers(headers: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """把请求头分为敏感头与非敏感头。返回 (non_sensitive, sensitive)。"""
    non_sensitive: dict[str, str] = {}
    sensitive: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            sensitive[key] = value
        else:
            non_sensitive[key] = value
    return non_sensitive, sensitive


@dataclass(frozen=True)
class CredentialRequest:
    """一条可携带凭证的出站请求的定义。"""

    #: 允许携带敏感头转发的精确 origin（scheme/host/port）。
    credential_origins: tuple[Origin, ...]
    #: 默认禁止自动重定向；None 表示禁止，否则为最大跳数（每跳重验）。
    max_redirects: int | None = None


@dataclass
class SafeRequest:
    """经过安全校验、可发送的请求（相对 URL 已合成为绝对 URL）。"""

    url: str
    origin: Origin
    headers: dict[str, str]
    method: str = "GET"
    data: bytes | None = None
    #: 是否允许携带敏感头（仅当该跳落在 credential origin）。
    carries_credentials: bool = False
    #: 固定的已验证解析结果（DNS rebinding 防护）。
    pinned_ips: tuple[str, ...] = ()
    #: 每请求覆盖的传输超时（None 用 transport 默认值）。
    connect_timeout: float | None = None
    read_timeout: float | None = None
    #: 允许携带敏感头转发的精确 origin 集合（重定向每跳重验用）。
    credential_origins: tuple[Origin, ...] = ()


@dataclass
class SafeTransportConfig:
    connect_timeout: float = 10.0
    read_timeout: float = 300.0
    max_response_bytes: int = 200 * 1024 * 1024  # 200MB
    max_redirects: int | None = None  # 应用级默认；credential 请求单独受限
