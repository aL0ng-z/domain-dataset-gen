"""T02 角色矩阵：viewer 只读、editor/reviewer/admin 写、非成员 403、未登录 401。

覆盖任务卡 §11 验收标准 2、3：
- viewer 对读操作成功、对写操作 403；
- editor/reviewer/admin 按既定矩阵通过；
- 未登录一律 401；非成员访问项目入口 403；
- 平铺 sections/chunks/candidates/cleaned-versions 对非成员 403/404，响应不泄露对象内容。
"""

import pytest
from httpx import AsyncClient

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# viewer 只读 / 写 403
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_viewer_read_ok_write_403(client: AsyncClient, full_resources):
    """viewer 读成功；写（DELETE/POST action）403。"""
    tokens = await _login(client, "viewer_user")
    r = full_resources["projects"]["a"]

    # 读
    res = await client.get(
        f"/api/projects/{r['pid']}/documents/{r['document'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 200, res.text

    # 写（DELETE 文档）-> 403
    res = await client.delete(
        f"/api/projects/{r['pid']}/documents/{r['document'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 403, res.text

    # 写（DELETE 数据集）-> 403
    res = await client.delete(
        f"/api/projects/{r['pid']}/datasets/{r['dataset'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 403, res.text

    # 写（POST task cancel）-> 403
    res = await client.post(
        f"/api/projects/{r['pid']}/tasks/{r['task'].id}/cancel",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# editor/reviewer/admin 写操作通过
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("username", ["editor_user", "reviewer_user", "admin_user"])
async def test_write_actions_pass_for_roles(client: AsyncClient, full_resources, username):
    """editor/reviewer/admin 对各自最低角色要求的写操作通过。"""
    tokens = await _login(client, username)
    r = full_resources["projects"]["a"]

    # editor 级：更新 curated item（PATCH）-> 200
    res = await client.patch(
        f"/api/projects/{r['pid']}/curated-items/{r['curated_item'].id}",
        headers=_bearer(tokens["access_token"]),
        json={"revision_note": "更新"},
    )
    assert res.status_code == 200, f"{username}: {res.status_code} {res.text}"

    # editor 级：删除数据集 -> 204
    res = await client.delete(
        f"/api/projects/{r['pid']}/datasets/{r['dataset'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 204, f"{username}: {res.status_code} {res.text}"


# ---------------------------------------------------------------------------
# 未登录 401、非成员 403
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unauthenticated_is_401(client: AsyncClient, full_resources):
    r = full_resources["projects"]["a"]
    res = await client.get(f"/api/projects/{r['pid']}/documents/{r['document'].id}")
    assert res.status_code == 401, res.text

    res2 = await client.get(f"/api/chunks/{r['chunk'].id}")
    assert res2.status_code == 401, res2.text


@pytest.mark.integration
async def test_non_member_project_entry_is_403(client: AsyncClient, make_user, make_project, db_session):
    """非成员访问项目入口（嵌套路由 require_project_member）-> 403。"""
    user = await make_user.create("outsider_role", "viewer")
    project = await make_project.create("外部项目", user.id)
    await db_session.commit()
    tokens = await _login(client, "outsider_role")

    res = await client.get(
        f"/api/projects/{project.id}/documents/",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 平铺路由非成员语义：403 或 404，不泄露对象内容
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "path_template",
    [
        lambda r: f"/api/sections/{r['section'].id}",
        lambda r: f"/api/chunks/{r['chunk'].id}",
        lambda r: f"/api/candidates/{r['candidate'].id}",
        lambda r: f"/api/cleaned-versions/{r['cleaned_version'].id}",
    ],
    ids=["section", "chunk", "candidate", "cleaned_version"],
)
async def test_flat_routes_non_member_no_leak(client: AsyncClient, full_resources, path_template):
    """平铺路由：项目 B 对象对非 B 成员（A 成员 editor）返回 403/404，不泄露对象内容。

    full_resources 中项目 B 仅 admin；editor 是非 B 成员。
    """
    tokens = await _login(client, "editor_user")
    r = full_resources["projects"]["b"]
    url = path_template(r)

    res = await client.get(url, headers=_bearer(tokens["access_token"]))
    assert res.status_code in (403, 404), res.text
    # 响应不得包含对象内容。
    assert "测试内容" not in res.text
    assert "# 清洗后" not in res.text
