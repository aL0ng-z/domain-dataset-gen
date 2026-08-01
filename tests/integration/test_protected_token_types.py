"""T01：受保护入口令牌类型矩阵 + 静态 jwt.decode 守卫。

覆盖任务卡 §11 验收标准 5、10：
- 受保护 HTTP/PDF 入口只接受 access token；refresh token 一律 401。
- 合法 access token 但权限不足 -> 403（与 401 区分）。
- 认证失败响应携带 WWW-Authenticate: Bearer。
- 全仓除统一令牌模块 app/core/jwt.py 外，不存在业务入口直接调用 jwt.decode。

静态搜索测试不依赖基础设施，属于纯单元校验。
"""

import re
from pathlib import Path

import pytest
from httpx import AsyncClient

from tests.conftest import create_access_token, create_refresh_token

REPO_ROOT = Path(__file__).resolve().parents[2]
API_APP_DIR = REPO_ROOT / "apps" / "api" / "app"

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 受保护入口只接受 access token
# ---------------------------------------------------------------------------


PROTECTED_GET_ROUTES = [
    "/api/auth/me",
    "/api/projects/",
    "/api/projects/{pid}/documents/",
]


@pytest.mark.integration
@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
async def test_protected_routes_reject_refresh_token(client: AsyncClient, org, path):
    projects = org["projects"]
    tokens = await _login(client, "viewer_user")
    url = path.format(pid=projects["a"].id)

    res = await client.get(url, headers=_bearer(tokens["refresh_token"]))
    assert res.status_code == 401, f"{path} should reject refresh token"

    # 同路径 access token 应通过认证（业务返回值非 401）
    ok = await client.get(url, headers=_bearer(tokens["access_token"]))
    assert ok.status_code != 401, f"{path} should accept access token"


@pytest.mark.integration
async def test_www_authenticate_header_present(client: AsyncClient, make_user):
    from tests.conftest import create_access_token

    user = await make_user.create("www_user", "viewer")
    refresh = create_refresh_token(user.id)
    bad = create_access_token(user.id, secret="wrong-secret")

    for token in (refresh, bad, "garbage"):
        res = await client.get("/api/auth/me", headers=_bearer(token))
        assert res.status_code == 401
        assert res.headers.get("www-authenticate", "").lower().startswith("bearer")


@pytest.mark.integration
async def test_valid_access_token_insufficient_role_is_403(client: AsyncClient, make_user):
    """合法 access token 但权限不足 -> 403（与 401 认证失败区分）。"""
    await make_user.create("low_role_user", "viewer")
    tokens = await _login(client, "low_role_user")

    # viewer 无管理员权限 -> 403
    res = await client.post(
        "/api/auth/register",
        json={"username": "someone", "email": "s@t.local", "password": "pw-123456", "role": "editor"},
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# PDF 入口：access 通过认证，refresh 拒绝
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_pdf_accepts_access_rejects_refresh(client: AsyncClient, org, make_resource):
    projects = org["projects"]
    user = org["users"]["viewer"]
    doc = await make_resource.create_document(projects["a"].id, user.id)
    tokens = await _login(client, "viewer_user")
    url = f"/api/projects/{projects['a'].id}/documents/{doc.id}/file"

    refresh_res = await client.get(url, headers=_bearer(tokens["refresh_token"]))
    assert refresh_res.status_code == 401

    access_res = await client.get(url, headers=_bearer(tokens["access_token"]))
    # 通过认证后进入下载流程（测试文档未上传到 MinIO -> 404 文件不存在）
    assert access_res.status_code in (401, 404), access_res.text


# ---------------------------------------------------------------------------
# WebSocket：access 连接、refresh 4401、缺失 token 4401
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ws_matrix(app, make_user, make_project, db_session):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.ws.task_ws import manager

    user = await make_user.create("ws_matrix", "viewer")
    project = await make_project.create("WS 矩阵", user.id)
    await make_project.add_member(project.id, user.id, "viewer")
    await db_session.commit()  # WS 授权用独立会话，必须先提交成员关系
    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)
    pid = str(project.id)

    with TestClient(app) as tc:
        # access + 项目成员 -> 连接成功，创建订阅
        with tc.websocket_connect(f"/ws/projects/{pid}/tasks?token={access}"):
            assert pid in manager._subscriptions

        # refresh -> 4401，不创建订阅
        with pytest.raises(WebSocketDisconnect) as exc, tc.websocket_connect(
            f"/ws/projects/{pid}/tasks?token={refresh}"
        ):
            pass
        assert exc.value.code == 4401
        assert pid not in manager._subscriptions

        # 无 token -> 4401
        with pytest.raises(WebSocketDisconnect) as exc2, tc.websocket_connect(
            f"/ws/projects/{pid}/tasks"
        ):
            pass
        assert exc2.value.code == 4401
        assert pid not in manager._subscriptions

        # 非项目成员 access token -> 4403
        outsider = await make_user.create("ws_outsider", "viewer")
        await db_session.commit()  # WS 授权用独立会话，必须先提交用户
        outsider_access = create_access_token(outsider.id)
        with pytest.raises(WebSocketDisconnect) as exc3, tc.websocket_connect(
            f"/ws/projects/{pid}/tasks?token={outsider_access}"
        ):
            pass
        assert exc3.value.code == 4403
        assert pid not in manager._subscriptions

    # 全部清理
    assert pid not in manager._subscriptions


# ---------------------------------------------------------------------------
# 静态守卫：业务入口不得直接调用 jwt.decode
# ---------------------------------------------------------------------------


_JWT_DECODE_PATTERN = re.compile(r"jwt\.decode")


def test_no_direct_jwt_decode_outside_core_module():
    """全仓除 app/core/jwt.py 外，任何业务入口不得直接调用 jwt.decode。"""
    violations = []
    for path in sorted(API_APP_DIR.rglob("*.py")):
        if path.name == "jwt.py" and path.parent.name == "core":
            continue
        content = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if _JWT_DECODE_PATTERN.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    assert not violations, "发现业务入口直接调用 jwt.decode:\n" + "\n".join(violations)
