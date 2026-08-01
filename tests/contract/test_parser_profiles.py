"""ParserProfile API 合同测试（T03 验收 #1/#9/#13）。

覆盖：任意层级/大小写变体 URL/token/secret 字段返回 422 且数据库无写入；
endpoint 列表安全投影；旧网络字段运行时拒绝；恶意 upload/results URL 时全局
Token/PDF 不发送。
"""


import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import ParserProfile
from parsing.transport import FakeSecureTransport, reset_transport, set_transport


async def _headers(user) -> dict[str, str]:
    """直接构造 JWT（T00 约定：显式传入 access token，避免测试内登录加重 DB 竞争）。"""
    from tests.conftest import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id, role=user.role)}"}


@pytest.fixture(autouse=True)
def _reset_transport_fixture():
    reset_transport()
    yield
    reset_transport()


UNSAFE_OPTIONS = [
    {"base_url": "https://evil.example.com"},  # 网络 URL 字段
    {"upload_url": "https://evil.example.com"},
    {"results_url": "https://evil.example.com"},
    {"vlm_base_url": "http://192.168.1.1:9000"},
    {"server_url": "https://evil.example.com"},
    {"proxy": "http://evil.example.com:8080"},
    {"headers": {"X-Evil": "1"}},
    {"api_key": "client-secret"},  # 秘密字段
    {"access_token": "client-token"},
    {"token": "client-token"},
    {"secret": "client-secret"},
    {"credential": "client-credential"},
    {"nested": {"base_url": "https://evil.example.com"}},  # 嵌套变体
    {"nested": {"deep": {"api_key": "secret"}}},
    {"API-KEY": "secret"},  # 大小写/连字符变体
    {"API_KEY": "secret"},  # 下划线变体
    {"aPi_K-eY": "secret"},  # 混合变体
    {"unknown_field": "anything"},  # 白名单外未知字段
    {"model_version": "vlm", "upload_url": "https://evil.example.com"},  # 合法+非法混合
]


async def test_create_rejects_unsafe_parser_options(client: AsyncClient, org):
    """任意层级/大小写变体 URL、token、secret、未知字段均返回 422。"""
    pid = org["projects"]["a"].id
    editor = await _headers(org["users"]["editor"])
    for options in UNSAFE_OPTIONS:
        resp = await client.post(
            f"/api/projects/{pid}/parser-profiles/",
            headers=editor,
            json={"name": "unsafe", "parser_name": "mineru", "parser_options": options},
        )
        assert resp.status_code == 422, f"options={options} 应返回 422，实际 {resp.status_code}: {resp.text}"
        assert "unsafe_parser_option" in resp.text or "invalid_parser_option" in resp.text


async def test_create_rejects_invalid_endpoint_ref(client: AsyncClient, org):
    pid = org["projects"]["a"].id
    resp = await client.post(
        f"/api/projects/{pid}/parser-profiles/",
        headers=await _headers(org["users"]["editor"]),
        json={
            "name": "bad-endpoint",
            "parser_name": "mineru",
            "parser_options": {"endpoint_ref": "no-such-endpoint", "model_version": "vlm"},
        },
    )
    assert resp.status_code == 422
    assert "invalid_parser_endpoint" in resp.text


async def test_unsafe_options_not_written_to_db(client: AsyncClient, org, db_session: AsyncSession):
    """422 后数据库无写入。"""
    pid = org["projects"]["a"].id
    await client.post(
        f"/api/projects/{pid}/parser-profiles/",
        headers=await _headers(org["users"]["editor"]),
        json={"name": "unsafe", "parser_name": "mineru", "parser_options": {"api_key": "x"}},
    )
    rows = (await db_session.execute(select(ParserProfile))).scalars().all()
    assert len(rows) == 0


async def test_endpoint_list_is_safe_projection(client: AsyncClient, org, monkeypatch):
    """端点列表只返回 endpoint_ref/display_name/parser_name/credential_configured。"""
    from app.security import registry as registry_module
    from app.security.registry import ParserEndpointConfig, ParserEndpointRegistry, reset_registry

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
    pid = org["projects"]["a"].id
    resp = await client.get(
        f"/api/projects/{pid}/parser-profiles/endpoints",
        headers=await _headers(org["users"]["viewer"]),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items == [
        {
            "endpoint_ref": "mineru-official",
            "display_name": "mineru-official",
            "parser_name": "mineru",
            "credential_configured": True,
        }
    ]
    # 不得返回主机/IP/端口/allowlist 或网络区域内部细节
    text = resp.text
    for secret in ("mineru.net", "aliyuncs", "credential_ref", "credential_origins", "network_zone"):
        assert secret not in text
    reset_registry()


async def test_response_never_echoes_legacy_url_or_secret(client: AsyncClient, org, db_session: AsyncSession):
    """旧 profile 的 URL/secret 不在响应中回显。"""
    from app.models.config import ParserProfile

    pid = org["projects"]["a"].id
    profile = ParserProfile(
        project_id=pid,
        name="legacy",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"base_url": "https://mineru.net/x", "api_key": "secret", "nested": {"token": "t"}},
    )
    db_session.add(profile)
    await db_session.flush()
    await db_session.refresh(profile)

    resp = await client.get(
        f"/api/projects/{pid}/parser-profiles/{profile.id}",
        headers=await _headers(org["users"]["viewer"]),
    )
    assert resp.status_code == 200
    assert "mineru.net" not in resp.text
    assert "secret" not in resp.text.lower() or resp.text == "secret"  # 字段值被脱敏
    options = resp.json()["parser_options"]
    assert options["base_url"] == "[REDACTED]"
    assert options["api_key"] == "[REDACTED]"
    assert options["nested"]["token"] == "[REDACTED]"


async def test_legacy_profile_triggers_409_before_pdf_download(client: AsyncClient, org, db_session: AsyncSession, monkeypatch):
    """含旧网络字段的 profile 触发解析返回 409，且不下载 PDF。"""
    from app.models.config import ParserProfile

    pid = org["projects"]["a"].id
    profile = ParserProfile(
        project_id=pid,
        name="legacy",
        version=1,
        is_default=False,
        parser_name="mineru",
        parser_options={"base_url": "https://mineru.net/x", "model_version": "vlm"},
    )
    db_session.add(profile)
    await db_session.flush()
    await db_session.refresh(profile)

    from app.models.document import Document

    doc = Document(
        project_id=pid,
        filename="sample.pdf",
        file_size=10,
        sha256="0" * 64,
        minio_key=f"tests/{org['users']['admin'].id}/sample.pdf",
        page_count=1,
        uploaded_by=org["users"]["admin"].id,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)

    # 若 worker 被触发（不应发生），会尝试下载 PDF → 用 fake transport 标记。
    fake = FakeSecureTransport(handler=lambda *a: (_ for _ in ()).throw(AssertionError("不应触发网络调用")))
    set_transport(fake)

    resp = await client.post(
        f"/api/projects/{pid}/documents/{doc.id}/parse",
        headers=await _headers(org["users"]["editor"]),
        json={"parser_profile_id": str(profile.id)},
    )
    assert resp.status_code == 409
    assert fake.calls == []
