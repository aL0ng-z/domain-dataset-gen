"""统一安全传输层：所有解析器出站调用必须经过本层（位于 libs/parsing）。

职责：
- 校验 URL 与 registry 允许集合（origin/端口/域后缀）；
- DNS 解析并校验全部 A/AAAA 地址为允许公网地址；固定已验证 IP 以防 DNS rebinding；
- 默认禁止自动重定向；有界重定向每跳重新应用策略，跨 origin 不转发敏感头；
- 限制连接/读取超时、最大响应体；拒绝非预期协议；
- 安全拒绝统一转换为可审计错误码，不把 URL query/响应体秘密写入 error_message；
- 记录型 fake transport：普通测试期间不产生任何真实 DNS/网络访问。

实现细节：底层使用 socket.create_connection((pinned_ip, port)) 并固定 hostname 的
SSL 上下文，使连接目标严格等于已验证 IP；HTTP 请求经 socket 直接构造，避免
urllib/requests 再次解析域名或自动跟随重定向。真实 HTTP 栈仅用于未启用安全层时的
纯解析器单测（本地不联网场景），安全层始终由 apps/api 注入。
"""

from __future__ import annotations

import json
import socket
import ssl
import zlib
from contextlib import suppress
from typing import Protocol
from urllib import parse as urllib_parse

from parsing.egress import (
    Origin,
    RedirectSafetyError,
    SafeRequest,
    SafeTransportConfig,
    UrlSafetyError,
    normalize_url,
    split_headers,
    validate_all_ips,
)

__all__ = [
    "SecureTransport",
    "FakeSecureTransport",
    "RedirectDetected",
    "HttpStatusError",
    "ResponseTooLarge",
    "InvalidEncoding",
    "InvalidJson",
    "get_transport",
    "set_transport",
    "reset_transport",
]


class Resolver(Protocol):
    def resolve(self, hostname: str) -> list[str]:
        ...


class DefaultResolver:
    """真实 DNS 解析（仅用于非测试环境）。"""

    def resolve(self, hostname: str) -> list[str]:
        return [addr[4][0] for addr in socket.getaddrinfo(hostname, None) if addr[0] in (socket.AF_INET, socket.AF_INET6)]


class SecureTransport:
    """经完整安全校验的 HTTP 客户端（阻塞式，供 asyncio.to_thread 使用）。"""

    def __init__(self, resolver: Resolver | None = None, config: SafeTransportConfig | None = None):
        self._resolver = resolver or DefaultResolver()
        self.config = config or SafeTransportConfig()

    def check_url(
        self,
        url: str,
        *,
        scheme: str | tuple[str, ...] = "https",
        allowed_port: int | None = None,
        allow_private_host: bool = False,
        read_timeout: float | None = None,
        connect_timeout: float | None = None,
        pinned_ips: tuple[str, ...] = (),
    ) -> SafeRequest:
        normalized, origin = normalize_url(url, scheme=scheme, allowed_port=allowed_port, allow_private_host=allow_private_host)
        pinned = pinned_ips or self._resolve_and_pin(origin)
        return SafeRequest(
            url=normalized,
            origin=origin,
            headers={},
            method="GET",
            pinned_ips=pinned,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

    def check_request(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None,
        headers: dict[str, str],
        scheme: str | tuple[str, ...] = "https",
        allowed_port: int | None = None,
        allow_private_host: bool = False,
        credential_origins: tuple[Origin, ...] = (),
        read_timeout: float | None = None,
        connect_timeout: float | None = None,
        pinned_ips: tuple[str, ...] = (),
    ) -> SafeRequest:
        normalized, origin = normalize_url(url, scheme=scheme, allowed_port=allowed_port, allow_private_host=allow_private_host)
        pinned = pinned_ips or self._resolve_and_pin(origin)
        non_sensitive, sensitive = split_headers(headers)
        carries = bool(sensitive) and any(
            origin.matches_host(c.host) and origin.port in (None, c.port, c.default_port) for c in credential_origins
        )
        merged = {**non_sensitive, **sensitive} if carries else non_sensitive
        return SafeRequest(
            url=normalized,
            origin=origin,
            headers=merged,
            method=method,
            data=data,
            carries_credentials=carries,
            pinned_ips=pinned,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            credential_origins=credential_origins,
        )

    def request(self, safe: SafeRequest) -> bytes:
        """发送请求并手动跟随有界重定向（每跳重新应用 scheme/origin/IP/凭证策略）。

        - 默认 max_redirects=0：凭证请求默认禁止自动重定向；
        - safe.read_timeout 与 connect_timeout 在整个链中生效。
        """
        current = safe
        for _hop in range((self.config.max_redirects or 0) + 1):
            try:
                return self._open(current)
            except RedirectDetected as redirect:
                if (self.config.max_redirects or 0) <= 0:
                    raise RedirectSafetyError(
                        "redirect_denied", "目标服务尝试重定向但策略禁止自动跟随"
                    ) from redirect
                if _hop >= self.config.max_redirects:
                    raise RedirectSafetyError(
                        "redirect_limit", "重定向跳数超过策略上限"
                    ) from redirect
                # 手动重定向：重新规范化、重新校验、跨 origin 不转发敏感头。
                next_safe = self._build_redirect_safe(current, redirect.location)
                if next_safe is None:
                    raise RedirectSafetyError(
                        "redirect_denied", "重定向目标不在允许集合内"
                    ) from redirect
                current = next_safe
        raise RedirectSafetyError("redirect_limit", "重定向跳数超过策略上限")

    def _build_redirect_safe(self, current: SafeRequest, location: str) -> SafeRequest | None:
        """按当前请求的安全上下文重新构建重定向请求；不匹配返回 None。"""
        try:
            normalized, origin = normalize_url(location, scheme="https")
        except UrlSafetyError:
            return None
        # 跨 origin 不转发敏感头：仅当新 origin 仍是 credential origin 才保留。
        non_sensitive, sensitive = split_headers(current.headers)
        if sensitive:
            carries = any(origin.matches_host(c.host) and origin.port in (None, c.port, c.default_port) for c in current.credential_origins)
        else:
            carries = False
        try:
            pinned = self._resolve_and_pin(origin)
        except Exception:
            return None
        merged = {**non_sensitive, **sensitive} if carries else non_sensitive
        return SafeRequest(
            url=normalized,
            origin=origin,
            headers=merged,
            method=current.method,
            data=current.data,
            carries_credentials=carries,
            pinned_ips=pinned,
            connect_timeout=current.connect_timeout,
            read_timeout=current.read_timeout,
        )

    def request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        allowed_port: int | None = None,
        allow_private_host: bool = False,
        credential_origins: tuple[Origin, ...] = (),
        read_timeout: float | None = None,
        connect_timeout: float | None = None,
        pinned_ips: tuple[str, ...] = (),
    ) -> tuple[dict, str]:
        safe = self.check_request(
            url,
            method=method,
            data=data,
            headers=headers or {},
            allowed_port=allowed_port,
            allow_private_host=allow_private_host,
            credential_origins=credential_origins,
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
            pinned_ips=pinned_ips,
        )
        body = self.request(safe)
        return self._parse_json(body), safe.url

    def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allowed_port: int | None = None,
        allow_private_host: bool = False,
        read_timeout: float | None = None,
        connect_timeout: float | None = None,
        pinned_ips: tuple[str, ...] = (),
    ) -> tuple[bytes, str]:
        safe = self.check_request(
            url,
            method="GET",
            data=None,
            headers=headers or {},
            allowed_port=allowed_port,
            allow_private_host=allow_private_host,
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
            pinned_ips=pinned_ips,
        )
        return self.request(safe), safe.url

    def request_raw(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        allowed_port: int | None = None,
        allow_private_host: bool = False,
        credential_origins: tuple[Origin, ...] = (),
        read_timeout: float | None = None,
        connect_timeout: float | None = None,
        pinned_ips: tuple[str, ...] = (),
    ) -> tuple[bytes, SafeRequest]:
        """发送一个任意方法的原始请求（返回 (body, safe)；body 不做 JSON 解析）。"""
        safe = self.check_request(
            url,
            method=method,
            data=data,
            headers=headers or {},
            allowed_port=allowed_port,
            allow_private_host=allow_private_host,
            credential_origins=credential_origins,
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
            pinned_ips=pinned_ips,
        )
        body = self.request(safe)
        return body, safe

    def _resolve_and_pin(self, origin: Origin) -> tuple[str, ...]:
        addresses = self._resolver.resolve(origin.host)
        if not addresses:
            from parsing.egress import IpSafetyError

            raise IpSafetyError("dns_failed", "无法解析任何地址")
        pinned = tuple(validate_all_ips(addresses))
        if not pinned:
            from parsing.egress import IpSafetyError

            raise IpSafetyError("unsafe_parser_ip", "解析结果不含允许地址")
        return pinned

    def _open(self, safe: SafeRequest) -> bytes:
        url, method, data, headers, carries = safe.url, safe.method, safe.data, safe.headers, safe.carries_credentials
        pinned_ips = safe.pinned_ips or self._resolve_and_pin(safe.origin)
        connect_timeout = safe.connect_timeout or self.config.connect_timeout
        read_timeout = safe.read_timeout or self.config.read_timeout
        host, port = self._host_port(url)
        last_error: Exception | None = None
        for ip in pinned_ips:
            try:
                return self._open_single(
                    ip, port, host, url, method, data, headers, carries, connect_timeout, read_timeout
                )
            except (TimeoutError, OSError) as exc:
                last_error = exc
                continue
        raise ConnectionError(f"无法连接解析目标: {last_error}")

    def _host_port(self, url: str) -> tuple[str, int]:
        parts = urllib_parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        return parts.hostname or "", port

    def _open_single(
        self,
        ip: str,
        port: int,
        host: str,
        url: str,
        method: str,
        data: bytes | None,
        headers: dict[str, str],
        carries_credentials: bool,
        connect_timeout: float,
        read_timeout: float,
    ) -> bytes:
        context = ssl.create_default_context()
        raw_sock = socket.create_connection((ip, port), timeout=connect_timeout)
        try:
            if url.lower().startswith("https:"):
                with context.wrap_socket(raw_sock, server_hostname=host) as sock:
                    sock.settimeout(read_timeout)
                    return self._http_exchange(sock, host, url, method, data, headers)
            with raw_sock:
                raw_sock.settimeout(read_timeout)
                return self._http_exchange(raw_sock, host, url, method, data, headers)
        finally:
            with suppress(OSError):
                raw_sock.close()

    def _http_exchange(self, sock, host: str, url: str, method: str, data: bytes | None, headers: dict[str, str]) -> bytes:
        parts = urllib_parse.urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        host_header = parts.netloc.split("@")[-1]
        request_headers = {
            "Host": host_header,
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        request_headers.update({k: v for k, v in headers.items() if k.lower() not in {"host", "connection", "accept-encoding"}})
        if data is not None:
            request_headers.setdefault("Content-Length", str(len(data)))
        header_lines = [f"{method} {path} HTTP/1.1"]
        for key, value in request_headers.items():
            header_lines.append(f"{key}: {value}")
        header_block = ("\r\n".join(header_lines) + "\r\n\r\n").encode("latin-1")
        sock.sendall(header_block + (data or b""))

        status_line = self._read_line(sock)
        if not status_line:
            raise ConnectionError("空响应")
        parts_status = status_line.split(None, 2)
        code = int(parts_status[1]) if len(parts_status) >= 2 else 0
        headers_out: dict[str, str] = {}
        while True:
            line = self._read_line(sock)
            if line == b"":
                break
            if b":" in line:
                key, _, value = line.partition(b":")
                headers_out[key.decode("latin-1").strip().lower()] = value.decode("latin-1").strip()
        body = self._read_body(sock, code, headers_out)
        if 300 <= code < 400:
            location = headers_out.get("location")
            if not location:
                raise ConnectionError("重定向缺少 Location")
            raise RedirectDetected(code, location, headers_out)
        if code >= 400:
            raise HttpStatusError(code, body)
        return body

    def _read_line(self, sock) -> bytes:
        chunks = []
        while True:
            byte = sock.recv(1)
            if not byte:
                break
            chunks.append(byte)
            if byte == b"\n":
                break
        return b"".join(chunks).rstrip(b"\r\n")

    def _read_body(self, sock, code: int, headers_out: dict[str, str]) -> bytes:
        transfer_encoding = headers_out.get("transfer-encoding", "").lower()
        content_length = headers_out.get("content-length")
        if "chunked" in transfer_encoding:
            body = self._read_chunked(sock)
        elif content_length is not None:
            length = int(content_length)
            if length > self.config.max_response_bytes:
                raise ResponseTooLarge(length)
            body = self._read_exact(sock, length)
        else:
            body = self._read_until_close(sock)
        encoding = headers_out.get("content-encoding", "").lower()
        if encoding in ("gzip", "deflate"):
            try:
                body = zlib.decompress(body, zlib.MAX_WBITS | 16) if encoding == "gzip" else zlib.decompress(body)
            except zlib.error as exc:
                raise InvalidEncoding("响应解压失败") from exc
            if len(body) > self.config.max_response_bytes:
                raise ResponseTooLarge(len(body))
        return body

    def _read_chunked(self, sock) -> bytes:
        body = b""
        while True:
            size_line = self._read_line(sock)
            if not size_line:
                break
            try:
                size = int(size_line.split(b";")[0].strip(), 16)
            except ValueError:
                raise InvalidEncoding("chunk 长度非法") from None
            if size == 0:
                while True:
                    if self._read_line(sock) == b"":
                        break
                break
            if len(body) + size > self.config.max_response_bytes:
                raise ResponseTooLarge(len(body) + size)
            body += self._read_exact(sock, size)
            self._read_line(sock)
        return body

    def _read_exact(self, sock, length: int) -> bytes:
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = sock.recv(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_until_close(self, sock) -> bytes:
        chunks = []
        total = 0
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > self.config.max_response_bytes:
                raise ResponseTooLarge(total)
            chunks.append(chunk)
        return b"".join(chunks)

    def _parse_json(self, body: bytes) -> dict:
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidJson("响应不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise InvalidJson("响应格式错误")
        return value


class RedirectDetected(Exception):  # noqa: N818
    def __init__(self, code: int, location: str, headers: dict[str, str]):
        super().__init__(f"redirect {code}")
        self.code = code
        self.location = location
        self.headers = headers


class HttpStatusError(Exception):  # noqa: N818
    def __init__(self, code: int, body: bytes):
        super().__init__(f"HTTP {code}")
        self.code = code
        self.body = body[:1000]


class ResponseTooLarge(Exception):  # noqa: N818
    pass


class InvalidEncoding(Exception):  # noqa: N818
    pass


class InvalidJson(Exception):  # noqa: N818
    pass


def _redact_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    redacted = [(k, "[REDACTED]" if "signature" in k.lower() or k.lower() in {"x-api-key", "token", "access_token"} else v) for k, v in query]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted), parts.fragment))


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: ("[REDACTED]" if k.lower() in {"authorization", "proxy-authorization", "x-api-key"} else v) for k, v in headers.items()}


class FakeSecureTransport:
    """记录型 fake transport：不产生任何真实 DNS/网络访问。

    由测试注册处理器。记录每次调用的 URL（脱敏 query）、方法、敏感头是否携带、body。
    未匹配的 URL 抛 AssertionError（fail closed）。
    """

    def __init__(self, handler=None):
        self._handler = handler
        self.calls: list[dict] = []
        self.requests: list[SafeRequest] = []

    @property
    def all_urls(self) -> list[str]:
        return [call["url"] for call in self.calls]

    @property
    def sensitive_header_calls(self) -> list[dict]:
        return [call for call in self.calls if call.get("has_authorization")]

    def check_url(self, url: str, **kwargs) -> SafeRequest:
        return SafeRequest(url=url, origin=Origin("https", "placeholder", None), headers={}, method="GET")

    def check_request(self, url: str, *, method: str, data: bytes | None, headers: dict[str, str], **kwargs) -> SafeRequest:
        non_sensitive, sensitive = split_headers(headers)
        carries = bool(sensitive)
        return SafeRequest(
            url=url,
            origin=Origin("https", "placeholder", None),
            headers={**non_sensitive, **sensitive} if carries else non_sensitive,
            method=method,
            data=data,
            carries_credentials=carries,
        )

    def request(self, safe: SafeRequest) -> bytes:
        return self._dispatch(safe.method, safe.url, safe.headers, safe.data)

    def request_raw(self, url: str, *, method: str, data: bytes | None = None, headers: dict[str, str] | None = None, **kwargs) -> tuple[bytes, SafeRequest]:
        safe = self.check_request(url, method=method, data=data, headers=headers or {})
        return self._dispatch(method, url, safe.headers, data), safe

    def request_json(self, url: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None, **kwargs) -> tuple[dict, str]:
        safe = self.check_request(url, method=method, data=data, headers=headers or {})
        body = self._dispatch(method, url, safe.headers, data)
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidJson("响应不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise InvalidJson("响应格式错误")
        return value, url

    def get_bytes(self, url: str, *, headers: dict[str, str] | None = None, **kwargs) -> tuple[bytes, str]:
        safe = self.check_request(url, method="GET", data=None, headers=headers or {})
        return self._dispatch("GET", url, safe.headers, None), url

    def _dispatch(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> bytes:
        self.calls.append(
            {
                "method": method,
                "url": _redact_url(url),
                "headers": _mask_headers(headers),
                "has_authorization": any(k.lower() == "authorization" for k in headers),
                "data": data,
            }
        )
        if self._handler is None:
            raise AssertionError(f"Fake transport 未注册处理器，拒绝调用: {method} {url}")
        result = self._handler(method, url, headers, data)
        if isinstance(result, bytes):
            return result
        return json.dumps(result).encode("utf-8")


_transport: SecureTransport | FakeSecureTransport | None = None


def get_transport() -> SecureTransport | FakeSecureTransport:
    global _transport
    if _transport is None:
        _transport = SecureTransport()
    return _transport


def set_transport(transport) -> None:
    global _transport
    _transport = transport


def reset_transport() -> None:
    global _transport
    _transport = None
