"""R02/R20：项目入口与有效角色必须依据真实成员关系。"""

import uuid

import pytest

from app.core.jwt import create_access_token


@pytest.mark.integration
async def test_project_metadata_requires_membership(client, org):
    pid = org["projects"]["b"].id
    headers = {"Authorization": f"Bearer {create_access_token(org['users']['viewer'].id)}"}
    for suffix in ("", "/members", "/access"):
        response = await client.get(f"/api/projects/{pid}{suffix}", headers=headers)
        assert response.status_code == 403, response.text
    for suffix in ("", "/members", "/access"):
        response = await client.get(f"/api/projects/{uuid.uuid4()}{suffix}", headers=headers)
        assert response.status_code == 404, response.text


@pytest.mark.integration
async def test_effective_role_uses_membership_not_global_role(client, make_user, make_project, db_session):
    owner = await make_user.create("owner", "admin")
    viewer = await make_user.create("global-viewer", "viewer")
    reviewer = await make_user.create("global-reviewer", "reviewer")
    project = await make_project.create("roles", owner.id)
    await make_project.add_member(project.id, viewer.id, "reviewer")
    await make_project.add_member(project.id, reviewer.id, "viewer")
    await db_session.flush()
    for user, role in ((owner, "admin"), (viewer, "reviewer"), (reviewer, "viewer")):
        response = await client.get(
            f"/api/projects/{project.id}/access",
            headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"effective_role": role}
