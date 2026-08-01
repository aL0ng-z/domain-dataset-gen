"""出站网络策略门面（apps/api）。统一实现位于 parsing.egress。"""

from parsing.egress import (  # noqa: F401
    CLOUD_METADATA_NETS,
    SENSITIVE_HEADERS,
    CredentialRequest,
    EgressSecurityError,
    IpSafetyError,
    Origin,
    RedirectSafetyError,
    SafeRequest,
    SafeTransportConfig,
    UrlSafetyError,
    canonicalize_hostname,
    classify_ip,
    normalize_url,
    split_headers,
    validate_all_ips,
)
