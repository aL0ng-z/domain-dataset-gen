"""最小 FastAPI 数据库集成测试：证明 conftest 底座可用。

覆盖：登录/me/refresh 主链路、无 token/伪造 token/伪造 role 的 401/403，
以及双项目 + 项目隔离的访问控制（通过 require_project_member 的文档端点验证）。

注册接口要求调用者是已登录 admin（首管理员由 seed 脚本直接写入数据库），
因此本测试用 UserFactory 直接创建用户再走 API 登录。数据由 db_session fixture
在测试后 TRUNCATE 清理。
"""

import pytest
from httpx import AsyncClient

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str, password: str = PASSWORD) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"{username} login failed: {res.text}"
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.mark.integration
async def test_login_me_and_refresh_flow(client: AsyncClient, make_user):
    await make_user.create("alice", "admin")

    # 登录
    login_res = await client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert login_res.status_code == 200, login_res.text
    tokens = login_res.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    # /auth/me
    me_res = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me_res.status_code == 200, me_res.text
    assert me_res.json()["username"] == "alice"

    # 刷新 token
    refresh_res = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_res.status_code == 200, refresh_res.text
    assert refresh_res.json()["access_token"]


@pytest.mark.integration
async def test_admin_can_register_new_user(client: AsyncClient, make_user):
    await make_user.create("boss", "admin")
    admin_headers = await _login(client, "boss")

    res = await client.post(
        "/api/auth/register",
        json={"username": "new_editor", "email": "new_editor@test.local", "password": "pw-123456", "role": "editor"},
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["username"] == "new_editor"


@pytest.mark.integration
async def test_auth_requires_token(client: AsyncClient):
    # 无 token -> 401
    res = await client.get("/api/auth/me")
    assert res.status_code == 401


@pytest.mark.integration
async def test_forged_token_rejected(client: AsyncClient):
    # 伪造 token -> 401
    res = await client.get("/api/auth/me", headers={"Authorization": "Bearer forged.invalid.token"})
    assert res.status_code == 401


@pytest.mark.integration
async def test_non_admin_cannot_register(client: AsyncClient, make_user):
    await make_user.create("editor_x", "editor")
    editor_headers = await _login(client, "editor_x")

    # editor 注册其它用户 -> 403
    res = await client.post(
        "/api/auth/register",
        json={"username": "another", "email": "another@test.local", "password": "pw-123456", "role": "editor"},
        headers=editor_headers,
    )
    assert res.status_code == 403


@pytest.mark.integration
async def test_two_projects_and_membership_isolation(client: AsyncClient, org):
    """验证双项目 fixture 可用，且非成员无法访问项目文档（require_project_member 强制）。"""
    projects = org["projects"]

    reviewer_headers = await _login(client, "reviewer_user")

    # 项目 A（成员）文档列表可访问
    res_a = await client.get(f"/api/projects/{projects['a'].id}/documents/", headers=reviewer_headers)
    assert res_a.status_code == 200, res_a.text

    # 项目 B（非成员）文档列表 -> 403
    res_b = await client.get(f"/api/projects/{projects['b'].id}/documents/", headers=reviewer_headers)
    assert res_b.status_code == 403, res_b.text


@pytest.mark.integration
async def test_org_fixture_has_four_roles(client: AsyncClient, org):
    """org fixture 应创建四个角色并都存在于项目 A。"""
    users = org["users"]
    assert set(users.keys()) == {"admin", "reviewer", "editor", "viewer"}

    for role in ("admin", "reviewer", "editor", "viewer"):
        res = await _login(client, f"{role}_user")
        assert res["Authorization"]

    # 项目成员列表应包含四人
    admin_headers = await _login(client, "admin_user")
    members = await client.get(f"/api/projects/{org['projects']['a'].id}/members", headers=admin_headers)
    assert members.status_code == 200, members.text
    assert len(members.json()) == 4
