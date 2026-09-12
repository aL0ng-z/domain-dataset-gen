"""默认解析配置、清空配置以及模板试跑与生成一致性回归。"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models.config import ModelConfig, ParserProfile
from app.services.config_service import ConfigService, ParserProfileService
from app.services.parse_freeze_service import freeze_profile_policy
from app.services.prompt_template_service import PromptTemplateService
from app.workers.parse_worker import _security_context_from_snapshot
from scripts.init_seed import seed_parser_specs


def test_local_mineru_crud_and_frozen_worker_share_endpoint_exemption():
    service = ParserProfileService(AsyncMock())
    options = {"model_path": "models/test-model", "render_dpi": 160}
    cleaned = service._validate_for_parser("mineru_local", options)
    service._resolve_endpoint_ref("mineru_local", cleaned)
    profile = ParserProfile(id=uuid.uuid4(), version=1, parser_name="mineru_local", parser_options=options)
    profile_snapshot, policy_snapshot, security = freeze_profile_policy(profile)
    assert profile_snapshot["parser_name"] == "mineru_local"
    assert policy_snapshot["endpoint_ref"] == "local"
    assert security == {}
    assert _security_context_from_snapshot(SimpleNamespace(endpoint_policy_snapshot=policy_snapshot)) == {}
    with pytest.raises(ValueError, match="unsafe_parser_option"):
        service._validate_for_parser("mineru_local", {"endpoint_ref": "unexpected-network"})


def test_seed_without_registry_has_only_valid_local_profiles(monkeypatch):
    from app.security.registry import ParserEndpointRegistry
    monkeypatch.setattr("scripts.init_seed.get_registry", lambda: ParserEndpointRegistry([]))
    specs = seed_parser_specs()
    assert {s["parser_name"] for s in specs} == {"pymupdf4llm", "mineru_local"}
    for spec in specs:
        freeze_profile_policy(ParserProfile(id=uuid.uuid4(), version=1, **spec))


def test_seed_registry_uses_ref_without_legacy_network_fields(monkeypatch):
    from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry
    registry = ParserEndpointRegistry([ParserEndpointConfig(
        endpoint_ref="mineru-official", parser_name="mineru",
        base_url="https://mineru.example/api", display_name="MinerU",
    )])
    monkeypatch.setattr("scripts.init_seed.get_registry", lambda: registry)
    monkeypatch.setattr("app.services.parse_freeze_service._registry", lambda: registry)
    for spec in seed_parser_specs():
        freeze_profile_policy(ParserProfile(id=uuid.uuid4(), version=1, **spec))
        if spec["parser_name"] == "mineru":
            assert spec["parser_options"] == {"model_version": "vlm", "endpoint_ref": "mineru-official"}


async def test_explicit_null_clears_optional_model_parameters():
    db = AsyncMock()
    service = ConfigService(db, ModelConfig)
    model = ModelConfig(id=uuid.uuid4(), version=1, temperature=0.5, max_tokens=99, extra_params={"seed": 7})
    service.get = AsyncMock(return_value=model)
    await service.update(model.id, temperature=None, max_tokens=None, extra_params=None)
    assert model.temperature is None and model.max_tokens is None and model.extra_params is None
    assert model.version == 2


def test_endpoint_credential_status_reads_actual_server_slot(monkeypatch):
    from pydantic import SecretStr

    from app.config import settings
    from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry
    registry = ParserEndpointRegistry([ParserEndpointConfig(
        endpoint_ref="mineru", parser_name="mineru", base_url="https://mineru.example/api",
        credential_ref="env:MINERU_API_TOKEN", credential_origins=["https://mineru.example"],
    )])
    monkeypatch.setattr(settings, "mineru_api_token", None)
    assert registry.list_for_ui()[0]["credential_configured"] is False
    monkeypatch.setattr(settings, "mineru_api_token", SecretStr("dummy-test"))
    assert registry.list_for_ui()[0]["credential_configured"] is True


@pytest.mark.parametrize("base_url", ["https://llm.example/v1", "https://llm.example/v1/"])
async def test_template_preview_uses_generation_client_and_renderer(monkeypatch, base_url):
    template = SimpleNamespace(
        id=uuid.uuid4(), version=1, name="QA", task_type="qa_generation",
        system_prompt="System", user_prompt_template="{{heading_path}}: {{content}}",
        input_schema=None, output_schema=None,
    )
    chunk = SimpleNamespace(content="source", heading_path="Chapter")
    model = SimpleNamespace(
        id=uuid.uuid4(), version=1, provider="local", base_url=base_url,
        api_key_encrypted="test-credential", model_name="test-model", temperature=None,
        max_tokens=None, extra_params={"seed": 7, "top_p": 0.8},
    )
    objects = iter([chunk, model])
    db = AsyncMock()
    db.execute.side_effect = lambda _: SimpleNamespace(scalar_one_or_none=lambda: next(objects))
    service = PromptTemplateService(db)
    service.get = AsyncMock(return_value=template)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, request=request, json={
            "id": "test", "object": "chat.completion", "created": 1, "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })
    async def send(_self, request, **_kwargs):
        return respond(request)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    result = await service.test_run(template.id, uuid.uuid4(), model.id)
    assert str(requests[0].url) == "https://llm.example/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert "temperature" not in body and "max_tokens" not in body
    assert body["seed"] == 7 and body["top_p"] == 0.8
    assert json.loads(result["input_prompt"]) == body["messages"]
    assert body["response_format"] == {"type": "json_object"}
    assert result["input_tokens"] == 3 and result["output_tokens"] == 2
