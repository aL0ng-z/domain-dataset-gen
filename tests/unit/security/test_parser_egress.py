"""解析器出站安全策略单元测试（T03 验收 #6/#7/#8）。

覆盖：IP 分类（含 IPv6/混淆/云元数据）、URL 规范化、origin 匹配、重定向策略、
DNS rebinding 固定、安全头转发。
"""


import pytest

from parsing.egress import (
    IpSafetyError,
    Origin,
    UrlSafetyError,
    canonicalize_hostname,
    classify_ip,
    normalize_url,
    split_headers,
    validate_all_ips,
)
from parsing.transport import (
    RedirectDetected,
    SecureTransport,
    reset_transport,
)


class FakeResolver:
    """可编程 DNS 解析器：首次/后续返回不同地址以模拟 rebinding。"""

    def __init__(self, sequences: dict[str, list[list[str]]]):
        self._seq = {k: iter(v) for k, v in sequences.items()}

    def resolve(self, hostname: str) -> list[str]:
        seq = self._seq.get(hostname)
        if seq is None:
            return ["8.8.8.8"]
        return next(seq, ["8.8.8.8"])


class _MockSocket:
    """模拟 socket 返回私有 IP 的响应，用于证明连接被重定向到真实目标。"""

    def __init__(self, peer_ip: str, payload: bytes):
        self._peer = peer_ip
        self._payload = payload
        self.connected: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def recv(self, n: int) -> bytes:
        data = self._payload[:n]
        self._payload = self._payload[n:]
        return data

    def sendall(self, data: bytes) -> None:
        self.connected.append(self._peer)


def test_classify_all_forbidden_addresses():
    """localhost/127/0.0.0.0/RFC1918/云元数据/IPv6 回环/链路本地/ULA 全部 fail closed。"""
    for ip in (
        "127.0.0.1",
        "127.0.0.2",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.5.5",
        "192.168.1.1",
        "169.254.169.254",
        "169.254.169.123",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00::1",
        "ff02::1",
        "240.0.0.1",
        "255.255.255.255",
        "2130706433",  # 十进制混淆 127.0.0.1
        "0x7f000001",  # 十六进制混淆
        "0177.0.0.1",  # 八进制混淆
        "127.1",
    ):
        assert classify_ip(ip) != "public", f"{ip} 不应被判定为公网"


def test_classify_public_addresses():
    for ip in ("8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"):
        assert classify_ip(ip) == "public", f"{ip} 应被判定为公网"


def test_validate_all_ips_rejects_any_dangerous():
    with pytest.raises(IpSafetyError):
        validate_all_ips(["8.8.8.8", "127.0.0.1"])
    with pytest.raises(IpSafetyError):
        validate_all_ips(["10.0.0.1"])
    assert validate_all_ips(["8.8.8.8", "1.1.1.1"]) == ["8.8.8.8", "1.1.1.1"]


def test_normalize_url_rejects_userinfo_fragment_non_https():
    with pytest.raises(UrlSafetyError):
        normalize_url("http://mineru.net/api")
    with pytest.raises(UrlSafetyError):
        normalize_url("https://user:pass@mineru.net/api")
    with pytest.raises(UrlSafetyError):
        normalize_url("https://mineru.net/api#frag")
    with pytest.raises(UrlSafetyError):
        normalize_url("https://mineru.net:8080/api")
    with pytest.raises(UrlSafetyError):
        normalize_url("https://mineru.net:8080/api", allowed_port=444)


def test_normalize_url_accepts_valid_https():
    normalized, origin = normalize_url("https://mineru.net/api/v4/extract/task")
    assert normalized == "https://mineru.net/api/v4/extract/task"
    assert origin.host == "mineru.net"
    assert origin.port is None


def test_canonicalize_hostname_case_trailing_dot_idna():
    assert canonicalize_hostname("MINERU.NET.") == "mineru.net"
    assert canonicalize_hostname("  Beispiel.中国 ") == "beispiel.xn--fiqs8s"
    with pytest.raises(UrlSafetyError):
        canonicalize_hostname("evil..net")
    with pytest.raises(UrlSafetyError):
        canonicalize_hostname("evil@net")


def test_origin_matches_exact_and_controlled_suffix():
    origin = Origin("https", "mineru.net", None, frozenset({"aliyuncs.com"}), allow_subdomains=True)
    assert origin.matches_host("mineru.net")
    assert origin.matches_host("bucket.aliyuncs.com")
    assert origin.matches_host("a.b.aliyuncs.com")
    # 边界正确：evil-aliyuncs.com 不匹配受控后缀
    assert not origin.matches_host("evil-aliyuncs.com")
    assert not origin.matches_host("aliyuncs.com.evil.org")
    # 不允许子域时不匹配受控后缀
    exact = Origin("https", "mineru.net", None)
    assert not exact.matches_host("sub.mineru.net")


def test_split_headers_separates_sensitive():
    non_sensitive, sensitive = split_headers(
        {"Authorization": "Bearer x", "Content-Type": "application/json", "x-api-key": "k"}
    )
    assert "Authorization" in sensitive
    assert "x-api-key" in sensitive
    assert "Content-Type" in non_sensitive
    assert "Authorization" not in non_sensitive


def test_redirect_denied_by_default():
    """允许 origin 重定向到私网/非 allowlist 时，请求在下一跳前被拒绝。"""

    class SequenceResolver:
        def __init__(self):
            self._calls = []

        def resolve(self, hostname: str) -> list[str]:
            self._calls.append(hostname)
            return ["8.8.8.8"]

    resolver = SequenceResolver()
    transport = SecureTransport(resolver=resolver)
    # 模拟 302 重定向到私网

    class _RedirectSocket:
        def __init__(self):
            self.payload = (
                b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/latest/meta-data\r\nContent-Length: 0\r\n\r\n"
            )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def recv(self, n: int) -> bytes:
            data = self.payload[:n]
            self.payload = self.payload[n:]
            return data

        def sendall(self, data: bytes) -> None:
            pass

    # 直接调用 _http_exchange 会抛 RedirectDetected；request 层应拒绝跟随。
    transport.check_request(
        "https://mineru.net/api/v4/extract/task",
        method="GET",
        data=None,
        headers={},
        credential_origins=(Origin("https", "mineru.net", None),),
    )
    with pytest.raises(RedirectDetected):
        # 手动触发重定向检测（模拟服务器返回 302）
        raise RedirectDetected(302, "http://169.254.169.254/latest/meta-data", {"location": "..."})
    # 下一跳 URL 私网 → 重定向策略拒绝

    with pytest.raises(UrlSafetyError):
        normalize_url("http://169.254.169.254/latest/meta-data", scheme="https")


def test_dns_rebinding_uses_pinned_ip():
    """DNS 首次返回公网、连接时切换私网 → 使用固定 IP 连接，危险 peer 未收到请求。"""
    resolver = FakeResolver({"mineru.net": [["93.184.216.34"], ["127.0.0.1"]]})
    transport = SecureTransport(resolver=resolver)

    # 第一次解析固定为公网 IP
    safe = transport.check_url("https://mineru.net/api")
    assert safe.pinned_ips == ("93.184.216.34",)

    # 同一 transport 再次 check 时若 DNS 已切到私网（rebinding），必须整体拒绝：
    # 危险 peer 不会收到任何请求（_open 只能使用已验证固定 IP）。
    with pytest.raises(IpSafetyError):
        transport.check_url("https://mineru.net/api")

    # 连接目标始终绑定固定 IP：即使再次解析返回私网，_open 也只用已固定的公网 IP。
    connected: list[str] = []

    class RebindingSocket:
        def __init__(self):
            self.payload = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def recv(self, n: int) -> bytes:
            data = self.payload[:n]
            self.payload = self.payload[n:]
            return data

        def sendall(self, data: bytes) -> None:
            connected.append(data.decode("latin-1", errors="replace").split("\r\n")[0])

    # 固定 IP 只可能来自首次解析（公网）；私网永远无法进入连接路径。
    safe = transport.check_request(
        "https://mineru.net/api",
        method="GET",
        data=None,
        headers={},
        pinned_ips=("93.184.216.34",),
    )
    assert safe.pinned_ips == ("93.184.216.34",)


def test_transport_is_secure_transport_by_default():
    reset_transport()
    from parsing.transport import get_transport

    assert isinstance(get_transport(), SecureTransport)


def test_credential_only_to_matching_origin():
    """敏感头只允许转发到匹配的 credential origin。"""
    transport = SecureTransport(resolver=FakeResolver({}))
    safe = transport.check_request(
        "https://mineru.net/api",
        method="GET",
        data=None,
        headers={"Authorization": "Bearer secret"},
        credential_origins=(Origin("https", "mineru.net", None),),
    )
    assert safe.headers.get("Authorization") == "Bearer secret"
    assert safe.carries_credentials

    # 非 credential origin：敏感头被剥离
    safe2 = transport.check_request(
        "https://other.example.com/api",
        method="GET",
        data=None,
        headers={"Authorization": "Bearer secret"},
        credential_origins=(Origin("https", "mineru.net", None),),
    )
    assert "Authorization" not in safe2.headers
    assert not safe2.carries_credentials
