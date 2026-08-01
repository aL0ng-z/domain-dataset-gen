import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from app.config import settings
from app.models.parse import ParseJob
from app.schemas.config import ParserProfileCreate, ParserProfileResponse
from app.services.parse_freeze_service import build_worker_options, freeze_profile_policy
from parsing.transport import FakeSecureTransport, get_transport, reset_transport, set_transport


def _fake_security() -> dict:
    return {
        "endpoint_ref": "mineru-official",
        "base_url": "https://mineru.net/api/v4/extract/task",
        "network_zone": "public-remote",
        "credential_origins": ["https://mineru.net"],
        "credential_ref": "env:MINERU_API_TOKEN",
    }


def _make_job() -> ParseJob:
    return ParseJob(id=uuid.uuid4(), document_id=uuid.uuid4(), parser_profile_id=uuid.uuid4(), status="queued")


def test_worker_injects_server_side_parser_token(monkeypatch):
    monkeypatch.setattr(settings, "mineru_api_token", SecretStr("server-secret"))
    options = build_worker_options(
        _make_job(),
        parser_name="mineru",
        stored_options={"model_version": "vlm"},
        security=_fake_security(),
        api_tokens={"mineru": settings.mineru_api_token, "paddleocr": settings.paddleocr_api_token},
    )
    assert options["api_key"] == "server-secret"
    # 安全上下文注入供 transport 校验 credential origin。
    assert options["_security"]["credential_origins"] == ["https://mineru.net"]


def test_token_not_injected_when_no_credential_ref():
    """security 无 credential_ref（如 managed-local）时不注入全局 Token。"""
    options = build_worker_options(
        _make_job(),
        parser_name="mineru_local_service",
        stored_options={"backend": "vlm-auto-engine"},
        security={"endpoint_ref": "ml-mineru", "base_url": "http://127.0.0.1:9010", "network_zone": "managed-local"},
        api_tokens={"mineru": SecretStr("server-secret")},
    )
    assert "api_key" not in options


def test_local_service_parser_does_not_require_remote_token():
    """managed-local 不携带远程 provider Token；本地服务解析器不需要 token。"""
    options = build_worker_options(
        _make_job(),
        parser_name="paddleocr_local_service",
        stored_options={"use_layout_detection": True},
        security={"endpoint_ref": "ml-paddleocr", "base_url": "http://127.0.0.1:9020", "network_zone": "managed-local"},
        api_tokens={"paddleocr": SecretStr("server-secret")},
    )
    assert options == {
        "use_layout_detection": True,
        "_security": {"endpoint_ref": "ml-paddleocr", "base_url": "http://127.0.0.1:9020", "network_zone": "managed-local"},
    }


def test_parser_profile_rejects_embedded_secret():
    with pytest.raises(ValidationError):
        ParserProfileCreate(name="unsafe", parser_name="mineru", parser_options={"api_key": "client-secret"})


def test_parser_profile_rejects_nested_and_variant_secret_keys():
    # 递归 + 大小写/连字符/下划线归一化都必须拒绝。
    with pytest.raises(ValidationError):
        ParserProfileCreate(name="nested", parser_name="mineru", parser_options={"nested": {"API-KEY": "x"}})
    with pytest.raises(ValidationError):
        ParserProfileCreate(name="nested2", parser_name="mineru", parser_options={"a": {"b": {"access_token": "x"}}})


def test_parser_profile_rejects_network_url_fields():
    for field in ("base_url", "upload_url", "results_url", "vlm_base_url", "server_url", "proxy"):
        with pytest.raises(ValidationError):
            ParserProfileCreate(name="net", parser_name="mineru", parser_options={field: "https://evil.example.com"})


def test_parser_profile_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ParserProfileCreate(name="unknown", parser_name="mineru", parser_options={"totally_new_option": 1})


def test_parser_profile_response_redacts_legacy_secret():
    profile = ParserProfileResponse(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="legacy",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"base_url": "https://mineru.net/api/v4/extract/task", "api_key": "legacy-secret"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    options = profile.model_dump()["parser_options"]
    # 递归脱敏：禁用字段一律替换为 [REDACTED]，不泄露 URL 或 secret。
    assert options == {
        "base_url": "[REDACTED]",
        "api_key": "[REDACTED]",
    }


def test_parser_profile_response_redacts_nested_secret_recursively():
    profile = ParserProfileResponse(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="legacy-nested",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"nested": {"api_key": "secret", "keep": 1}},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    options = profile.model_dump()["parser_options"]
    assert options == {"nested": {"api_key": "[REDACTED]", "keep": 1}}


def test_freeze_profile_rejects_network_urls_for_remote_parser(monkeypatch):
    """含旧网络字段的 profile 在冻结时 fail closed。"""
    from app.models.config import ParserProfile
    from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry, reset_registry

    from app.security import registry as registry_module

    monkeypatch.setattr(
        registry_module,
        "build_registry",
        lambda configs=None: ParserEndpointRegistry(
            [
                ParserEndpointConfig(
                    endpoint_ref="mineru-official",
                    parser_name="mineru",
                    network_zone="public-remote",
                    base_url="https://mineru.net/api/v4/extract/task",
                    credential_ref="env:MINERU_API_TOKEN",
                    credential_origins=["https://mineru.net"],
                )
            ]
        ),
    )
    reset_registry()

    profile = ParserProfile(
        id=uuid.uuid4(), project_id=uuid.uuid4(), name="legacy", version=1,
        parser_name="mineru",
        parser_options={"base_url": "https://mineru.net/api/v4/extract/task", "model_version": "vlm"},
    )
    from app.services.parse_freeze_service import ParseFreezeError

    with pytest.raises(ParseFreezeError) as excinfo:
        freeze_profile_policy(profile)
    assert excinfo.value.code == "unsafe_parser_option"
    reset_registry()


def test_fake_transport_is_used_by_default_in_tests():
    """普通测试默认使用记录型 fake，不产生真实 DNS/网络访问。"""
    set_transport(FakeSecureTransport())
    assert isinstance(get_transport(), FakeSecureTransport)
    reset_transport()
