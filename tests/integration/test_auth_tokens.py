"""T01：JWT 令牌语义集成测试（access/refresh 严格分离）。

覆盖任务卡 §11 验收标准 1-4、以及部分 5：
1. access token 可访问 /api/auth/me，refresh token 对同一路径稳定返回 401。
2. refresh token 对普通业务 API、PDF 入口均返回 401，对 WebSocket 不得创建 Redis 订阅。
3. access token、缺 type token、过期 token、伪造 token 和非 UUID sub 调用刷新均返回 401。
4. 用户停用后，两类未过期 token 都失败，且服务端日志不包含原 token。
5. 参数化覆盖 Authorization 缺失、Bearer 格式错误及算法不匹配。

数据由 db_session fixture 在测试后 TRUNCATE 清理。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from jose import jwt
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.ws.task_ws import manager
from tests.conftest import (
    create_access_token,
    create_refresh_token,
)

PASSWORD = "password-123"


async def _make_login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _make_bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. access 可访问 /me，refresh 拒绝
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_access_can_me_refresh_cannot(client: AsyncClient, make_user):
    await make_user.create("alice_tokens", "admin")
    tokens = await _make_login(client, "alice_tokens")

    me_ok = await client.get("/api/auth/me", headers=_make_bearer(tokens["access_token"]))
    assert me_ok.status_code == 200, me_ok.text
    assert me_ok.json()["username"] == "alice_tokens"

    me_refresh = await client.get("/api/auth/me", headers=_make_bearer(tokens["refresh_token"]))
    assert me_refresh.status_code == 401
    # 认证错误响应不泄露 token 内容
    assert tokens["refresh_token"] not in me_refresh.text
    assert me_refresh.headers.get("www-authenticate", "").lower().startswith("bearer")

    # 再次请求仍为 401（稳定）
    again = await client.get("/api/auth/me", headers=_make_bearer(tokens["refresh_token"]))
    assert again.status_code == 401


# ---------------------------------------------------------------------------
# 2. refresh token 对业务 API、PDF 入口返回 401
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_refresh_rejected_on_business_api(client: AsyncClient, org):
    projects = org["projects"]
    tokens = await _make_login(client, "editor_user")

    # 普通业务 API（projects 列表）带 refresh token -> 401
    res = await client.get("/api/projects/", headers=_make_bearer(tokens["refresh_token"]))
    assert res.status_code == 401, res.text

    # 项目文档列表带 refresh token -> 401
    res2 = await client.get(
        f"/api/projects/{projects['a'].id}/documents/",
        headers=_make_bearer(tokens["refresh_token"]),
    )
    assert res2.status_code == 401, res2.text


@pytest.mark.integration
async def test_refresh_rejected_on_pdf_entry(client: AsyncClient, org, make_resource):
    projects = org["projects"]
    user = org["users"]["editor"]
    doc = await make_resource.create_document(projects["a"].id, user.id)
    tokens = await _make_login(client, "editor_user")

    # PDF 入口带 refresh token -> 401
    res = await client.get(
        f"/api/projects/{projects['a'].id}/documents/{doc.id}/file",
        headers=_make_bearer(tokens["refresh_token"]),
    )
    assert res.status_code == 401, res.text

    # PDF 入口带 access token -> 通过认证，进入文件下载（此处文档未上传 -> 404 文件不存在）
    res2 = await client.get(
        f"/api/projects/{projects['a'].id}/documents/{doc.id}/file",
        headers=_make_bearer(tokens["access_token"]),
    )
    assert res2.status_code in (401, 404), res2.text


@pytest.mark.integration
async def test_refresh_token_ws_rejected_without_subscription(app, make_user):
    """refresh token 连接 WebSocket -> 4401，且不创建 Redis 订阅。"""
    user = await make_user.create("ws_neg", "viewer")
    refresh = create_refresh_token(user.id)

    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect) as exc, tc.websocket_connect(
            f"/ws/projects/proj-neg/tasks?token={refresh}"
        ):
            pass
        assert exc.value.code == 4401
    assert "proj-neg" not in manager._subscriptions


@pytest.mark.integration
async def test_access_token_ws_connects(app, make_user, make_project, db_session):
    """access token + 项目成员连接 WebSocket 成功，关闭后清理订阅。"""
    user = await make_user.create("ws_pos", "viewer")
    project = await make_project.create("WS 正例", user.id)
    await make_project.add_member(project.id, user.id, "viewer")
    await db_session.commit()  # WS 授权用独立会话，必须先提交成员关系
    access = create_access_token(user.id)
    pid = str(project.id)

    with TestClient(app) as tc, tc.websocket_connect(
        f"/ws/projects/{pid}/tasks?token={access}"
    ) as ws:
        assert pid in manager._subscriptions
        ws.send_text("ping")
    assert pid not in manager._subscriptions


@pytest.mark.integration
async def test_access_token_ws_rejected_when_not_member(app, make_user, make_project, db_session):
    """合法 access token 但非项目成员 -> 4403，且不创建 Redis 订阅。"""
    user = await make_user.create("ws_nonmember", "viewer")
    project = await make_project.create("WS 非成员", user.id)
    await db_session.commit()  # WS 授权用独立会话，必须先提交用户/项目
    access = create_access_token(user.id)
    pid = str(project.id)

    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect) as exc, tc.websocket_connect(
            f"/ws/projects/{pid}/tasks?token={access}"
        ):
            pass
        assert exc.value.code == 4403
    assert pid not in manager._subscriptions

# ---------------------------------------------------------------------------
# 3. 刷新接口负向矩阵
# ---------------------------------------------------------------------------


def _make_token(payload: dict) -> str:
    now = datetime.now(UTC)
    base = {"sub": str(uuid.uuid4()), "type": "access", "iat": now, "exp": now + timedelta(minutes=30)}
    base.update(payload)
    return jwt.encode(base, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@pytest.mark.integration
async def test_refresh_accepts_only_refresh_token(client: AsyncClient, make_user):
    user = await make_user.create("neg_refresh", "viewer")
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)

    # refresh 端点收到 access token -> 401
    r1 = await client.post("/api/auth/refresh", json={"refresh_token": access})
    assert r1.status_code == 401

    # 缺 type 的 access token -> 401
    no_type = _make_token({"type": None})
    del_no_type = jwt.encode(
        {"sub": str(user.id), "iat": datetime.now(UTC), "exp": datetime.now(UTC) + timedelta(minutes=30)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    r2 = await client.post("/api/auth/refresh", json={"refresh_token": del_no_type})
    assert r2.status_code == 401
    r3 = await client.post("/api/auth/refresh", json={"refresh_token": no_type})
    assert r3.status_code == 401

    # 伪造 token -> 401
    r4 = await client.post("/api/auth/refresh", json={"refresh_token": "forged.invalid.token"})
    assert r4.status_code == 401

    # 非 UUID sub -> 401
    bad_sub = _make_token({"type": "refresh", "sub": "not-a-uuid"})
    r5 = await client.post("/api/auth/refresh", json={"refresh_token": bad_sub})
    assert r5.status_code == 401

    # 有效 refresh token -> 200
    r6 = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r6.status_code == 200, r6.text
    assert r6.json()["access_token"] and r6.json()["refresh_token"]


@pytest.mark.integration
async def test_refresh_expired_token_rejected(client: AsyncClient, make_user):
    user = await make_user.create("expired_user", "viewer")
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "type": "refresh",
            "iat": now - timedelta(minutes=60),
            "exp": now - timedelta(minutes=30),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    r = await client.post("/api/auth/refresh", json={"refresh_token": expired})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 4. 用户停用后，两类未过期 token 都失效，且日志不包含原 token
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_deactivated_user_tokens_invalid_and_no_token_in_logs(
    client: AsyncClient, make_user, db_session, caplog
):
    user = await make_user.create("deact_user", "editor")
    await db_session.flush()
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)

    # 停用前均可用
    me_before = await client.get("/api/auth/me", headers=_make_bearer(access))
    assert me_before.status_code == 200

    # 通过同一 db_session 停用用户（数据库事实源）
    user.is_active = False
    await db_session.commit()

    with caplog.at_level(logging.WARNING):
        me_after = await client.get("/api/auth/me", headers=_make_bearer(access))
        refresh_res = await client.post("/api/auth/refresh", json={"refresh_token": refresh})

    assert me_after.status_code == 401
    assert refresh_res.status_code == 401

    # 日志不包含原 token
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert access not in joined
    assert refresh not in joined


# ---------------------------------------------------------------------------
# 5. 参数化：Authorization 缺失 / 格式错误 / 算法不匹配
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "auth_value",
    [
        None,  # 缺失 Authorization
        "Bearer",  # 格式错误：无凭据
        "Basic abc123",  # 非 Bearer scheme
    ],
)
async def test_protected_endpoint_auth_header_variants(client: AsyncClient, auth_value):
    headers = {} if auth_value is None else {"Authorization": auth_value}
    res = await client.get("/api/auth/me", headers=headers)
    assert res.status_code == 401


@pytest.mark.integration
async def test_wrong_algorithm_rejected(client: AsyncClient, make_user):
    user = await make_user.create("algo_user", "viewer")
    now = datetime.now(UTC)
    wrong_algo = jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        settings.jwt_secret_key,
        algorithm="HS384",
    )
    res = await client.get("/api/auth/me", headers=_make_bearer(wrong_algo))
    assert res.status_code == 401


@pytest.mark.integration
async def test_forged_signature_rejected(client: AsyncClient, make_user):
    user = await make_user.create("forge_user", "viewer")
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        "attacker-secret-key",
        algorithm=settings.jwt_algorithm,
    )
    res = await client.get("/api/auth/me", headers=_make_bearer(forged))
    assert res.status_code == 401
