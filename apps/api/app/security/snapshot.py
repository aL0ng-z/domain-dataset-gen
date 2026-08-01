"""快照/脱敏门面（apps/api）。统一实现位于 parsing.snapshot。"""

from parsing.snapshot import (  # noqa: F401
    ALLOWED_OPTION_FIELDS,
    SNAPSHOT_SCHEMA_VERSION,
    allowed_fields_for,
    build_profile_snapshot,
    canonicalize,
    find_forbidden_keys,
    is_forbidden_option_key,
    mask_authorization_headers,
    normalized_key,
    policy_snapshot_sha256,
    profile_snapshot_sha256,
    redact,
    redact_url,
    scan_for_secrets,
    validate_parser_options,
)
