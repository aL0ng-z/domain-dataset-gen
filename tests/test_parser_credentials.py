import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from app.config import settings
from app.schemas.config import ParserProfileCreate, ParserProfileResponse
from app.workers.parse_worker import _parser_options_with_credentials


def test_worker_injects_server_side_parser_token(monkeypatch):
    monkeypatch.setattr(settings, "mineru_api_token", SecretStr("server-secret"))

    options = _parser_options_with_credentials("mineru", {"base_url": "https://mineru.net/api/v4/extract/task"})

    assert options["api_key"] == "server-secret"


def test_local_service_parser_does_not_require_remote_token(monkeypatch):
    monkeypatch.setattr(settings, "mineru_api_token", SecretStr("server-secret"))

    options = _parser_options_with_credentials("mineru_local_service", {"base_url": "http://127.0.0.1:9010"})

    assert options == {"base_url": "http://127.0.0.1:9010"}


def test_paddleocr_local_service_parser_does_not_require_remote_token(monkeypatch):
    monkeypatch.setattr(settings, "paddleocr_api_token", SecretStr("server-secret"))

    options = _parser_options_with_credentials(
        "paddleocr_local_service", {"base_url": "http://127.0.0.1:9020/layout-parsing"}
    )

    assert options == {"base_url": "http://127.0.0.1:9020/layout-parsing"}


def test_parser_profile_rejects_embedded_secret():
    with pytest.raises(ValidationError):
        ParserProfileCreate(name="unsafe", parser_name="mineru", parser_options={"api_key": "client-secret"})


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
