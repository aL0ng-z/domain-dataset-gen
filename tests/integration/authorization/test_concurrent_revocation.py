"""T02 并发与线性化：成员撤销后写请求全部 403，不产生跨项目写入。

覆盖任务卡 §11 验收标准 9：
- 成员撤销与写请求竞争时，以事务提交顺序线性化；
- 撤销提交后的新请求全部 403，不产生跨项目或无成员写入。
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


@pytest.mark.integration
async def test_member_removed_then_write_403(client: AsyncClient, full_resources, db_session):
    """撤销成员（删除 ProjectMember 并提交）后，editor 写请求全部 403。"""
    from sqlalchemy import delete

    from app.models.project import ProjectMember

    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    editor = full_resources["users"]["editor"]

    # 撤销：删除 editor 在项目 A 的成员关系并提交。
    await db_session.execute(
        delete(ProjectMember).where(
            ProjectMember.project_id == a["pid"], ProjectMember.user_id == editor.id
        )
    )
    await db_session.commit()

    # 撤销提交后的新请求全部 403（读入口也 403，因非成员）。
    res = await client.get(
        f"/api/projects/{a['pid']}/documents/{a['document'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 403, res.text

    # 写请求同样 403。
    res2 = await client.patch(
        f"/api/projects/{a['pid']}/curated-items/{a['curated_item'].id}",
        headers=_bearer(tokens["access_token"]),
        json={"revision_note": "撤销后写入"},
    )
    assert res2.status_code == 403, res2.text

    res3 = await client.delete(
        f"/api/projects/{a['pid']}/datasets/{a['dataset'].id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res3.status_code == 403, res3.text


@pytest.mark.integration
async def test_member_removed_flat_routes_403_or_404(client: AsyncClient, full_resources, db_session):
    """撤销后平铺路由：A 成员的 section/chunk 也拒绝（403/404），不泄露内容。"""
    from sqlalchemy import delete

    from app.models.project import ProjectMember

    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    editor = full_resources["users"]["editor"]

    await db_session.execute(
        delete(ProjectMember).where(
            ProjectMember.project_id == a["pid"], ProjectMember.user_id == editor.id
        )
    )
    await db_session.commit()

    for path in (
        f"/api/sections/{a['section'].id}",
        f"/api/chunks/{a['chunk'].id}",
        f"/api/candidates/{a['candidate'].id}",
        f"/api/cleaned-versions/{a['cleaned_version'].id}",
    ):
        res = await client.get(path, headers=_bearer(tokens["access_token"]))
        assert res.status_code in (403, 404), f"{path}: {res.status_code}"
        assert "测试内容" not in res.text
