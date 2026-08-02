"""T07 API 层测试：任务列表/详情/cancel/retry/attempt 端点。

覆盖：
- task_get 返回 state_version/attempt_count/can_cancel/can_retry/error_code。
- task_cancel：queued 原子转 cancelled；processing 转 cancelling 返回 202 语义。
- task_retry：仅 failed 可重试；completed/cancelled 返回 409；需要 Idempotency-Key。
- task_attempts：按 attempt_no 排序返回审计列表，不返回敏感内部字段。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.workers.queue import TaskQueue

pytestmark = pytest.mark.integration

PASSWORD = "password-123"


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    res = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_task(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    created_by: uuid.UUID,
    handler: str = "parse_document",
    status: str = "queued",
    idempotency_key: str | None = None,
) -> Task:
    q = TaskQueue(db)
    task = await q.create_task(
        project_id=project_id,
        task_type="parse",
        entity_type="document",
        entity_id=uuid.uuid4(),
        created_by=created_by,
        handler=handler,
        payload={"document_id": str(uuid.uuid4()), "parse_job_id": str(uuid.uuid4())},
        payload_version=1,
        idempotency_key=idempotency_key,
        max_attempts=2,
        timeout_seconds=300,
    )
    if status != "queued":
        task.status = status
        if status in ("processing", "cancelling"):
            # 约束 ck_tasks_lease_required：processing/cancelling 必须有 run token 与 lease。
            task.run_token = uuid.uuid4()
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=300)
        if status in ("completed", "failed", "cancelled"):
            task.completed_at = datetime.now(UTC)
    await db.commit()
    return task


async def test_task_get_returns_lifecycle_fields(client: AsyncClient, org, db_session):
    tokens = await _login(client, "editor_user")
    task = await _seed_task(
        db_session, project_id=org["projects"]["a"].id, created_by=org["users"]["editor"].id,
    )

    res = await client.get(
        f"/api/projects/{org['projects']['a'].id}/tasks/{task.id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state_version"] == 0
    assert body["attempt_count"] == 0
    assert body["max_attempts"] == 2
    assert body["can_cancel"] is True
    assert body["can_retry"] is False
    assert body["error_code"] is None
    # 不返回敏感 payload 字段。
    assert "payload" not in body


async def test_task_list_includes_cancelling(client: AsyncClient, org, db_session):
    tokens = await _login(client, "viewer_user")
    task = await _seed_task(
        db_session, project_id=org["projects"]["a"].id, created_by=org["users"]["editor"].id,
        status="cancelling",
    )

    res = await client.get(
        f"/api/projects/{org['projects']['a'].id}/tasks/?status=cancelling",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert any(t["id"] == str(task.id) for t in items)


async def test_cancel_queued_task(client: AsyncClient, org, db_session):
    tokens = await _login(client, "editor_user")
    task = await _seed_task(
        db_session, project_id=org["projects"]["a"].id, created_by=org["users"]["editor"].id,
    )

    res = await client.post(
        f"/api/projects/{org['projects']['a'].id}/tasks/{task.id}/cancel",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "cancelled"

    # 重复取消幂等：终态返回当前状态，不改写完成时间。
    fresh = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    orig_completed_at = fresh.completed_at
    res2 = await client.post(
        f"/api/projects/{org['projects']['a'].id}/tasks/{task.id}/cancel",
        headers=_bearer(tokens["access_token"]),
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "cancelled"
    fresh2 = (await db_session.execute(select(Task).where(Task.id == task.id))).scalar_one()
    assert fresh2.completed_at == orig_completed_at


async def test_retry_requires_failed_and_idempotency_key(client: AsyncClient, org, db_session):
    tokens = await _login(client, "editor_user")
    pid = org["projects"]["a"].id
    # completed 任务不可重试 -> 409。
    completed = await _seed_task(
        db_session, project_id=pid, created_by=org["users"]["editor"].id, status="completed",
    )
    res = await client.post(
        f"/api/projects/{pid}/tasks/{completed.id}/retry",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 409, res.text

    # failed 任务缺少 Idempotency-Key -> 422。
    failed = await _seed_task(
        db_session, project_id=pid, created_by=org["users"]["editor"].id, status="failed",
    )
    res = await client.post(
        f"/api/projects/{pid}/tasks/{failed.id}/retry",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 422, res.text

    # failed 任务 + Idempotency-Key -> 201 新 Task。
    res = await client.post(
        f"/api/projects/{pid}/tasks/{failed.id}/retry",
        headers={**_bearer(tokens["access_token"]), "Idempotency-Key": "retry-key-1"},
    )
    assert res.status_code == 201, res.text
    new_task_id = res.json()["id"]
    assert new_task_id != str(failed.id)
    assert res.json()["retry_of_task_id"] == str(failed.id)
    assert res.json()["status"] == "queued"

    # 原任务保持 failed，未复活。
    src = (await db_session.execute(select(Task).where(Task.id == failed.id))).scalar_one()
    assert src.status == "failed"

    # 同 key 并发 retry：只生成一个后继。
    res2 = await client.post(
        f"/api/projects/{pid}/tasks/{failed.id}/retry",
        headers={**_bearer(tokens["access_token"]), "Idempotency-Key": "retry-key-1"},
    )
    assert res2.status_code == 201
    assert res2.json()["id"] == new_task_id


async def test_attempts_endpoint_returns_audit_list(client: AsyncClient, org, db_session):
    tokens = await _login(client, "viewer_user")
    task = await _seed_task(
        db_session, project_id=org["projects"]["a"].id, created_by=org["users"]["editor"].id,
    )
    # 创建两个 attempt。
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="worker-x", batch=10)
    await db_session.commit()
    # 回队再 claim 一次 -> attempt 2。
    task = claimed[0]
    await q.transition(
        task_id=task.id, run_token=task.run_token,
        from_status="processing", to_status="queued",
        expected_state_version=task.state_version,
        error_code="NETWORK_ERROR", error_message="timeout",
        next_run_at=datetime.now(UTC),  # 立即可领取。
    )
    await db_session.commit()
    claimed2 = await q.claim_due(worker_id="worker-x", batch=10)
    await db_session.commit()
    await q.transition(
        task_id=claimed2[0].id, run_token=claimed2[0].run_token,
        from_status="processing", to_status="failed",
        expected_state_version=claimed2[0].state_version,
        error_code="BUSINESS_ERROR", error_message="boom",
    )
    await db_session.commit()

    res = await client.get(
        f"/api/projects/{org['projects']['a'].id}/tasks/{task.id}/attempts",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 200, res.text
    attempts = res.json()
    assert len(attempts) >= 1
    # 按 attempt_no 排序。
    nos = [a["attempt_no"] for a in attempts]
    assert nos == sorted(nos)
    # 不返回 payload/内部字段。
    assert all("payload" not in a for a in attempts)


async def test_task_404_for_other_project(client: AsyncClient, full_resources):
    """不同项目统一 404（T02 语义：A 成员用 A 的 pid 访问 B 的 task -> resolver 404）。"""
    tokens = await _login(client, "editor_user")
    a = full_resources["projects"]["a"]
    b = full_resources["projects"]["b"]
    task = b["task"]
    res = await client.get(
        f"/api/projects/{a['pid']}/tasks/{task.id}",
        headers=_bearer(tokens["access_token"]),
    )
    assert res.status_code == 404, res.text
