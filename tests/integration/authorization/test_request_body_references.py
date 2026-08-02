"""T02 请求体跨项目引用：写入前被拒绝，数据库无部分写入、后台任务未派发。

覆盖任务卡 §11 验收标准 5：
- 跨项目 parser profile、prompt/model config、curated item、dataset/benchmark 引用
  在写入前被拒绝（404），数据库无部分写入且后台任务未派发。
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
async def test_cross_project_parser_profile_rejected(client: AsyncClient, full_resources, db_session):
    """项目 A 触发解析引用项目 B 的 parser profile -> 404，不创建 ParseJob/Task。"""
    from sqlalchemy import func, select

    from app.models.parse import ParseJob
    from app.models.task import Task

    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    b = full_resources["projects"]["b"]

    before_jobs = (await db_session.execute(select(func.count()).select_from(ParseJob))).scalar()
    before_tasks = (await db_session.execute(select(func.count()).select_from(Task))).scalar()

    res = await client.post(
        f"/api/projects/{a['pid']}/documents/{a['document'].id}/parse",
        headers=_bearer(tokens["access_token"]),
        json={"parser_profile_id": str(b["parser_profile"].id)},
    )
    assert res.status_code == 404, res.text

    await db_session.commit()
    after_jobs = (await db_session.execute(select(func.count()).select_from(ParseJob))).scalar()
    after_tasks = (await db_session.execute(select(func.count()).select_from(Task))).scalar()
    assert after_jobs == before_jobs, "跨项目 parser profile 不得产生 ParseJob"
    assert after_tasks == before_tasks, "跨项目 parser profile 不得派发任务"


@pytest.mark.integration
async def test_cross_project_prompt_model_config_rejected(client: AsyncClient, full_resources, db_session):
    """批量生成引用跨项目 prompt template / model config -> 404，不派发任务。"""
    from sqlalchemy import func, select

    from app.models.task import Task

    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    b = full_resources["projects"]["b"]

    before_tasks = (await db_session.execute(select(func.count()).select_from(Task))).scalar()

    # 跨项目 prompt template
    res = await client.post(
        f"/api/projects/{a['pid']}/documents/{a['document'].id}/generate-batch",
        headers=_bearer(tokens["access_token"]),
        json={
            "prompt_template_id": str(b["prompt_template"].id),
            "model_config_id": str(a["model_config"].id),
        },
    )
    assert res.status_code == 404, res.text

    # 跨项目 model config
    res2 = await client.post(
        f"/api/projects/{a['pid']}/documents/{a['document'].id}/generate-batch",
        headers=_bearer(tokens["access_token"]),
        json={
            "prompt_template_id": str(a["prompt_template"].id),
            "model_config_id": str(b["model_config"].id),
        },
    )
    assert res2.status_code == 404, res2.text

    await db_session.commit()
    after_tasks = (await db_session.execute(select(func.count()).select_from(Task))).scalar()
    assert after_tasks == before_tasks, "跨项目引用不得派发任务"


@pytest.mark.integration
async def test_cross_project_curated_and_dataset_rejected(client: AsyncClient, full_resources, db_session):
    """dataset add_item 引用跨项目 curated item -> 404；curated add-to-dataset 跨项目 dataset -> 404。"""
    from sqlalchemy import func, select

    from app.models.dataset import DatasetItem

    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    b = full_resources["projects"]["b"]

    before_items = (await db_session.execute(select(func.count()).select_from(DatasetItem))).scalar()

    # 项目 A 数据集添加项目 B 的 curated item -> 404
    res = await client.post(
        f"/api/projects/{a['pid']}/datasets/{a['dataset'].id}/items",
        headers=_bearer(tokens["access_token"]),
        json={"curated_item_id": str(b["curated_item"].id)},
    )
    assert res.status_code == 404, res.text

    # 项目 A curated item 添加到项目 B 的 dataset -> 404
    res2 = await client.post(
        f"/api/projects/{a['pid']}/curated-items/{a['curated_item'].id}/add-to-dataset",
        headers=_bearer(tokens["access_token"]),
        json={"dataset_id": str(b["dataset"].id)},
    )
    assert res2.status_code == 404, res2.text

    await db_session.commit()
    after_items = (await db_session.execute(select(func.count()).select_from(DatasetItem))).scalar()
    assert after_items == before_items, "跨项目引用不得产生 DatasetItem"


@pytest.mark.integration
async def test_cross_project_export_profile_rejected(client: AsyncClient, full_resources, db_session):
    """导出引用跨项目 export profile -> 404，不派发导出任务。"""
    from sqlalchemy import func, select

    from app.models.task import Task

    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    b = full_resources["projects"]["b"]

    before_tasks = (await db_session.execute(select(func.count()).select_from(Task))).scalar()

    res = await client.post(
        f"/api/projects/{a['pid']}/datasets/{a['dataset'].id}/export",
        headers=_bearer(tokens["access_token"]),
        json={"export_profile_id": str(b["export_profile"].id)},
    )
    assert res.status_code == 404, res.text

    await db_session.commit()
    after_tasks = (await db_session.execute(select(func.count()).select_from(Task))).scalar()
    assert after_tasks == before_tasks, "跨项目 export profile 不得派发任务"
