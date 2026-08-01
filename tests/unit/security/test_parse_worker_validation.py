"""parse_worker 执行前复核与错误脱敏单元测试（T03 验收 #3/#13）。

覆盖：
- worker 在下载 PDF/联网前校验冻结快照：缺失、hash 不匹配、schema 不支持、
  含禁用秘密字段均失败；
- 错误消息递归脱敏，不包含 URL query、响应体秘密或 PDF 内容。
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.parse import ParseJob
from app.security.snapshot import build_profile_snapshot, profile_snapshot_sha256
from app.workers.parse_worker import _safe_error_message, _validate_frozen_snapshot


def _complete_job() -> ParseJob:
    snapshot = build_profile_snapshot(
        profile_id=str(uuid.uuid4()),
        version=1,
        parser_name="mineru",
        endpoint_ref="mineru-official",
        functional_options={"model_version": "vlm"},
    )
    policy = {
        "endpoint_ref": "mineru-official",
        "parser_name": "mineru",
        "network_zone": "public-remote",
        "credential_ref": "env:MINERU_API_TOKEN",
        "credential_origins": ["https://mineru.net"],
    }
    return ParseJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        parser_profile_id=uuid.uuid4(),
        status="queued",
        snapshot_schema_version=1,
        parser_profile_snapshot=snapshot,
        parser_profile_sha256=profile_snapshot_sha256(snapshot, 1),
        endpoint_policy_snapshot=policy,
        endpoint_policy_ref="mineru-official",
        endpoint_policy_version="1",
        endpoint_policy_sha256="b" * 64,
        frozen_at=datetime.now(UTC),
    )


def test_valid_snapshot_passes_validation():
    _validate_frozen_snapshot(_complete_job())  # 不应抛异常


def test_missing_snapshot_fails():
    job = _complete_job()
    job.parser_profile_snapshot = None
    with pytest.raises(RuntimeError):
        _validate_frozen_snapshot(job)


def test_hash_mismatch_fails():
    job = _complete_job()
    job.parser_profile_sha256 = "f" * 64  # 与重算不符
    with pytest.raises(RuntimeError):
        _validate_frozen_snapshot(job)


def test_legacy_unavailable_fails():
    job = _complete_job()
    job.snapshot_schema_version = 0
    with pytest.raises(RuntimeError):
        _validate_frozen_snapshot(job)


def test_snapshot_with_secret_fails():
    job = _complete_job()
    job.parser_profile_snapshot = {
        "profile_id": str(uuid.uuid4()),
        "version": 1,
        "parser_name": "mineru",
        "endpoint_ref": "mineru-official",
        "options": {"api_key": "leaked-secret"},
    }
    with pytest.raises(RuntimeError):
        _validate_frozen_snapshot(job)


def test_safe_error_message_redacts_signed_url():
    exc = ValueError(
        "下载失败: https://oss.example.com/bucket/key.pdf?X-Amz-Signature=abc123&X-Amz-Expires=3600"
    )
    message = _safe_error_message(exc)
    # 签名值必须被脱敏；key 名可保留但不得泄露签名值。
    assert "abc123" not in message
    assert "X-Amz-Signature=[REDACTED]" in message
    assert "[REDACTED]" in message


def test_safe_error_message_redacts_authorization_header():
    exc = ValueError("HTTP 401: Authorization: Bearer sk-super-secret-token")
    message = _safe_error_message(exc)
    assert "sk-super-secret-token" not in message
    assert "[REDACTED]" in message


def test_safe_error_message_truncates_long_content():
    exc = ValueError("PDF 内容: " + "A" * 5000)
    message = _safe_error_message(exc)
    assert len(message) <= 2000
