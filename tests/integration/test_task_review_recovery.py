"""用户流程 R09–R11/R21：任务与业务对象恢复、执行策略和长期 runner。"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.models.config import TaskPolicy
from app.models.section import CleaningJob
from app.models.task import Task, TaskAttempt
from app.services.config_service import ConfigService
from app.services.task_service import TaskService
from app.workers.execution import HandlerRegistry
from app.workers.lifecycle import lifecycle_registry
from app.workers.queue import TaskQueue
from app.workers.runner import TaskRunner
from tests.integration.test_task_api import _bearer, _login

pytestmark = pytest.mark.integration


async def _task(db, org, *, task_type="test", handler="test:review", payload=None, attempts=2, timeout=60):
    return await TaskQueue(db).create_task(
        project_id=org["projects"]["a"].id, created_by=org["users"]["admin"].id,
        task_type=task_type, handler=handler, entity_type="document", entity_id=uuid.uuid4(),
        payload=payload or {}, max_attempts=attempts, timeout_seconds=timeout,
    )


async def _job_task(db, org, make_resource, kind):
    pid, uid = org["projects"]["a"].id, org["users"]["admin"].id
    doc = await make_resource.create_document(pid, uid)
    profile = await make_resource.create_parser_profile(pid)
    parse_job = await make_resource.create_parse_job(doc.id, profile.id)
    payload = {"document_id": str(doc.id), "parse_job_id": str(parse_job.id)}
    job = parse_job
    if kind == "clean":
        job = CleaningJob(document_id=doc.id, parse_job_id=parse_job.id, started_by=uid, status="queued")
        db.add(job)
        await db.flush()
        payload["cleaning_job_id"] = str(job.id)
    task = await _task(db, org, task_type=kind, handler=f"{kind}_document", payload=payload)
    await db.commit()
    await db.refresh(job)
    return job, task


@pytest.mark.parametrize("kind", ["parse", "clean"])
async def test_cancel_queued_converges_job_without_running_handler(db_session, org, make_resource, kind):
    job, task = await _job_task(db_session, org, make_resource, kind)
    assert job.task_id == task.id
    await TaskService(db_session).cancel_task(task.id, org["users"]["admin"].id)
    await db_session.commit()
    await db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.error_code == "TASK_CANCELLED"
    assert job.completed_at is not None
    assert await TaskQueue(db_session).claim_due(worker_id="unused") == []


@pytest.mark.parametrize("kind", ["parse", "clean"])
async def test_reaper_retries_crash_then_fails_job_and_retry_rebinds(
    db_session, org, make_resource, kind,
):
    job, task = await _job_task(db_session, org, make_resource, kind)
    queue = TaskQueue(db_session)
    await queue.claim_due(worker_id="crashed")
    token = task.run_token
    await db_session.execute(update(Task).where(Task.id == task.id).values(
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=2)))
    await db_session.commit()
    stats = await queue.reap_expired(lease_grace_seconds=0)
    await db_session.commit()
    await db_session.refresh(job)
    assert stats["requeued"] == 1 and job.status == "queued"
    assert not await queue.heartbeat(task_id=task.id, run_token=token)
    await db_session.execute(update(Task).where(Task.id == task.id).values(next_run_at=datetime.now(UTC)))
    await db_session.commit()
    await queue.claim_due(worker_id="crashed-again")
    await db_session.execute(update(Task).where(Task.id == task.id).values(
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=2)))
    await queue.reap_expired(lease_grace_seconds=0)
    await db_session.commit()
    await db_session.refresh(task)
    await db_session.refresh(job)
    assert task.status == job.status == "failed"
    assert job.error_code == "TEMPORARY_INFRA_ERROR"
    retry, created = await TaskService(db_session).retry_task(
        task, idempotency_key="manual", created_by=org["users"]["admin"].id)
    await db_session.commit()
    await db_session.refresh(job)
    assert created and retry.retry_of_task_id == task.id
    assert job.status == "queued" and job.task_id == retry.id and job.completed_at is None
    # A late predecessor callback must not overwrite a manually retried job.
    await lifecycle_registry.terminal(db_session, task, "failed", "late callback")
    await db_session.commit()
    await db_session.refresh(job)
    assert job.status == "queued" and job.error_message is None


async def test_policy_freezes_zero_retries_and_manual_retry_inherits(db_session, org):
    policy = TaskPolicy(project_id=org["projects"]["a"].id, name="once", task_type="parse",
                        is_default=True, max_retries=0, timeout_seconds=17, concurrency_limit=1)
    db_session.add(policy)
    await db_session.flush()
    task = await _task(db_session, org, task_type="parse", attempts=9, timeout=99)
    assert task.max_attempts == 1 and task.timeout_seconds == 17
    snapshot = dict(task.policy_snapshot)
    await TaskQueue(db_session).claim_due(worker_id="worker")
    await TaskQueue(db_session).transition(task_id=task.id, run_token=task.run_token,
        from_status="processing", to_status="failed", expected_state_version=task.state_version,
        error_code="BUSINESS_ERROR", error_message="failed")
    policy.max_retries, policy.timeout_seconds = 8, 999
    await db_session.flush()
    retry, _ = await TaskService(db_session).retry_task(task, idempotency_key="retry",
                                                      created_by=org["users"]["admin"].id)
    assert retry.max_attempts == 1 and retry.timeout_seconds == 17
    assert retry.policy_snapshot == snapshot


async def test_default_policy_is_per_type_and_claim_limit_is_live(db_session, org, _test_session_factory):
    pid = org["projects"]["a"].id
    policies = [TaskPolicy(project_id=pid, name=name, task_type=kind, concurrency_limit=1)
                for name, kind in [("generate-1", "generate"), ("generate-2", "generate"),
                                   ("batch", "generate_batch")]]
    db_session.add_all(policies)
    await db_session.flush()
    service = ConfigService(db_session, TaskPolicy)
    for policy in policies:
        await service.set_default(pid, policy.id)
    assert not policies[0].is_default and policies[1].is_default and policies[2].is_default
    t1 = await _task(db_session, org, task_type="generate")
    t2 = await _task(db_session, org, task_type="generate")
    parent = await _task(db_session, org, task_type="generate_batch")
    await db_session.commit()
    queue = TaskQueue(db_session)
    claimed = await queue.claim_due(worker_id="one", batch=10)
    await db_session.commit()
    assert {t.id for t in claimed} == {t1.id, parent.id}
    await queue.request_cancel(task_id=t1.id, cancel_requested_by=org["users"]["admin"].id)
    await db_session.commit()
    async with _test_session_factory() as other:
        assert await TaskQueue(other).claim_due(worker_id="two") == []
        await other.commit()
    # Increasing the live cap permits the second task, regardless of its frozen budget.
    policies[1].concurrency_limit = 2
    await db_session.commit()
    assert [t.id for t in await queue.claim_due(worker_id="two")] == [t2.id]


async def test_parallel_claim_respects_global_policy_cap(db_session, org, _test_session_factory):
    db_session.add(TaskPolicy(project_id=org["projects"]["a"].id, name="one", task_type="parse",
                              is_default=True, concurrency_limit=1))
    for _ in range(3):
        await _task(db_session, org, task_type="parse")
    await db_session.commit()
    async def claim(worker):
        async with _test_session_factory() as db:
            rows = await TaskQueue(db).claim_due(worker_id=worker)
            await db.commit()
            return [t.id for t in rows]
    results = await asyncio.gather(claim("a"), claim("b"))
    assert sum(map(len, results)) == 1


async def test_runner_claims_one_and_maintains_heartbeat_during_handler(
    db_session, org, _test_session_factory, monkeypatch,
):
    import app.workers.runner as runner_module
    monkeypatch.setattr(runner_module, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    entered, release = asyncio.Event(), asyncio.Event()
    registry = HandlerRegistry()
    @registry.register("test:review", 1)
    async def handler(ctx):
        entered.set()
        await release.wait()
    first = await _task(db_session, org)
    second = await _task(db_session, org)
    await db_session.commit()
    runner = TaskRunner(_test_session_factory, registry)
    execution = asyncio.create_task(runner._claim_and_execute())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await asyncio.sleep(0.15)
        async with _test_session_factory() as observer:
            a, b = await observer.get(Task, first.id), await observer.get(Task, second.id)
            assert a.status == "processing" and a.heartbeat_at > a.started_at
            assert b.status == "queued" and b.attempt_count == 0 and b.run_token is None
    finally:
        release.set()
        await asyncio.wait_for(execution, 10)


async def test_reaper_runs_while_handler_waits(db_session, org, _test_session_factory, monkeypatch):
    import app.workers.runner as runner_module
    monkeypatch.setattr(runner_module, "REAP_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(runner_module, "IDLE_POLL_INTERVAL_SECONDS", 0.01)
    entered, release = asyncio.Event(), asyncio.Event()
    registry = HandlerRegistry()
    @registry.register("test:review", 1)
    async def handler(ctx):
        entered.set()
        await release.wait()
    expired = await _task(db_session, org, attempts=1)
    await TaskQueue(db_session).claim_due(worker_id="dead")
    current = await _task(db_session, org)
    await db_session.commit()
    runner = TaskRunner(_test_session_factory, registry)
    execution = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with _test_session_factory() as setup:
            await setup.execute(update(Task).where(Task.id == expired.id).values(
                lease_expires_at=datetime.now(UTC) - timedelta(minutes=2)))
            await setup.commit()
        for _ in range(50):
            async with _test_session_factory() as observer:
                state = await observer.get(Task, expired.id)
                live = await observer.get(Task, current.id)
                if state.status == "failed":
                    assert live.status == "processing"
                    break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("long-running handler blocked the reaper")
    finally:
        runner.stop()
        release.set()
        await asyncio.wait_for(execution, 10)


async def test_failed_business_callback_rolls_back_task_terminal(db_session, org, monkeypatch):
    async def broken(_db, _task, _status, _error):
        raise RuntimeError("business write unavailable")
    monkeypatch.setitem(lifecycle_registry._handlers, ("test:review", 1), broken)
    task = await _task(db_session, org)
    task_id = task.id
    await db_session.commit()
    with pytest.raises(RuntimeError, match="business write unavailable"):
        await TaskQueue(db_session).request_cancel(task_id=task_id,
            cancel_requested_by=org["users"]["admin"].id)
    await db_session.rollback()
    assert (await db_session.get(Task, task_id)).status == "queued"


async def test_expired_token_cannot_be_revived_or_publish(db_session, org):
    task = await _task(db_session, org)
    queue = TaskQueue(db_session)
    await queue.claim_due(worker_id="expired")
    token, version = task.run_token, task.state_version
    await db_session.execute(update(Task).where(Task.id == task.id).values(
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert not await queue.heartbeat(task_id=task.id, run_token=token)
    assert not await queue.refresh_lease(task_id=task.id, run_token=token, timeout_seconds=60)
    assert not await queue.transition(task_id=task.id, run_token=token, from_status="processing",
                                     to_status="completed", expected_state_version=version)


async def test_parent_poll_does_not_spend_retry_budget(db_session, org):
    task = await _task(db_session, org, task_type="generate_batch", handler="generate_batch", attempts=2)
    queue = TaskQueue(db_session)
    for _ in range(3):
        await queue.claim_due(worker_id="poller")
        token, attempt, version = task.run_token, task.attempt_count, task.state_version
        await queue.transition(task_id=task.id, run_token=token, from_status="processing",
            to_status="queued", expected_state_version=version, next_run_at=datetime.now(UTC))
        await queue.finish_attempt(task_id=task.id, run_token=token, attempt_no=attempt, status="completed")
    await queue.claim_due(worker_id="poller")
    assert task.attempt_count == 4 and await queue.has_retry_budget(task)
    assert len((await db_session.execute(select(TaskAttempt))).scalars().all()) == 4


async def test_clean_reentry_ignores_cancelled_job_and_returns_live_task(client, db_session, org, make_resource):
    job, task = await _job_task(db_session, org, make_resource, "clean")
    from app.models.parse import ParseJob
    parse = await db_session.get(ParseJob, job.parse_job_id)
    parse.status = "completed"
    await db_session.commit()
    login = await client.post("/api/auth/login", json={"username": "admin_user", "password": "password-123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    url = f"/api/projects/{org['projects']['a'].id}/documents/{job.document_id}/cleaning/start"
    live = await client.post(url, json={"parse_job_id": str(job.parse_job_id)}, headers=headers)
    assert live.status_code == 202 and live.json()["task_id"] == str(task.id)
    assert live.json()["reused"] is True
    await TaskService(db_session).cancel_task(task.id, org["users"]["admin"].id)
    await db_session.commit()
    fresh = await client.post(url, json={"parse_job_id": str(job.parse_job_id)}, headers=headers)
    assert fresh.status_code == 202 and fresh.json()["reused"] is False
    assert fresh.json()["cleaning_job_id"] != str(job.id)
    assert fresh.json()["task_id"] != str(task.id)


async def test_generation_task_retry_derives_batch_preserving_frozen_policy(db_session, org, monkeypatch):
    from app.models.generation_batch import GenerationBatch
    from app.services.generation_retry_service import GenerationRetryPlanner
    from app.workers.generate_worker import run_generate_batch_handler, run_generate_single_handler
    from tests.integration.test_generation_flow import (
        FakeLLM,
        _build_generation_doc,
        _claim_task_async,
        _create_batch,
        _run_handler_and_transition,
    )
    pid = org["projects"]["a"].id
    uid = org["users"]["editor"].id
    policies = [TaskPolicy(project_id=pid, name=kind, task_type=kind, is_default=True,
                           max_retries=0, timeout_seconds=timeout, concurrency_limit=1)
                for kind, timeout in [("generate_batch", 91), ("generate", 92)]]
    db_session.add_all(policies)
    res = await _build_generation_doc(db_session, org, chunk_count=1)
    fake = FakeLLM({0: '{"question":"Q","answer":"A"}'}, fail_json_for={0})
    fake.install(monkeypatch)
    batch, parent = await _create_batch(db_session, org=org, doc=res["doc"], tpl=res["tpl"],
                                         model=res["model"], selected=None)
    await db_session.commit()
    child = await db_session.scalar(select(Task).where(Task.parent_task_id == parent.id))
    child = (await _claim_task_async(db_session, child))[0]
    child = await _run_handler_and_transition(db_session, child, run_generate_single_handler)
    await db_session.refresh(parent)
    parent = (await _claim_task_async(db_session, parent))[0]
    parent = await _run_handler_and_transition(db_session, parent, run_generate_batch_handler)
    await db_session.refresh(child)
    await db_session.refresh(batch)
    assert child.status == parent.status == "failed"
    for policy in policies:
        await db_session.refresh(policy)
        policy.max_retries, policy.timeout_seconds = 8, 900
    await db_session.commit()
    service = TaskService(db_session)
    retry, created = await service.retry_task(parent, idempotency_key="same", created_by=uid)
    await db_session.commit()
    assert created and retry.entity_id != batch.id and retry.retry_of_task_id == parent.id
    assert retry.max_attempts == 1 and retry.timeout_seconds == 91
    new_child = await db_session.scalar(select(Task).where(Task.parent_task_id == retry.id))
    assert new_child.max_attempts == 1 and new_child.timeout_seconds == 92
    assert new_child.policy_snapshot == child.policy_snapshot
    replay, created = await service.retry_task(parent, idempotency_key="same", created_by=uid)
    assert replay.id == retry.id and not created
    conflict, created = await service.retry_task(parent, idempotency_key="different", created_by=uid)
    assert conflict is None and not created
    same_batch, same_task = await GenerationRetryPlanner(db_session, service).plan_retry(
        source_batch=batch, project_id=pid, created_by=uid, idempotency_key="same")
    assert same_task.id == retry.id and same_batch.id == retry.entity_id
    await db_session.refresh(parent)
    assert parent.status == "failed"
    assert (await db_session.get(GenerationBatch, batch.id)).status == "failed"


async def test_saturated_backlog_does_not_hide_runnable_other_type(db_session, org):
    db_session.add(TaskPolicy(project_id=org["projects"]["a"].id, name="one", task_type="parse",
                              is_default=True, concurrency_limit=1))
    await _task(db_session, org, task_type="parse")
    await TaskQueue(db_session).claim_due(worker_id="occupied")
    for _ in range(101):
        await _task(db_session, org, task_type="parse")
    runnable = await _task(db_session, org, task_type="clean")
    assert [t.id for t in await TaskQueue(db_session).claim_due(worker_id="free")] == [runnable.id]


async def test_batch_waits_for_remaining_child_before_terminal_summary(db_session, org, monkeypatch):
    from app.models.generation_batch import GenerationBatch
    from app.workers.generate_worker import run_generate_batch_handler, run_generate_single_handler
    from tests.integration.test_generation_flow import (
        FakeLLM,
        _build_generation_doc,
        _claim_task_async,
        _create_batch,
        _run_handler_and_transition,
    )
    res = await _build_generation_doc(db_session, org, chunk_count=2)
    FakeLLM({0: '{"question":"Q0","answer":"A0"}', 1: '{"question":"Q1","answer":"A1"}'}, fail_json_for={0}).install(monkeypatch)
    batch, parent = await _create_batch(db_session, org=org, doc=res["doc"], tpl=res["tpl"],
                                         model=res["model"], selected=None)
    parent_id, batch_id = parent.id, batch.id
    first_chunk_id, second_chunk_id = (str(res["chunks"][0].id), str(res["chunks"][1].id))
    await db_session.commit()
    children = (await db_session.execute(select(Task).where(
        Task.parent_task_id == parent_id).order_by(Task.created_at))).scalars().all()
    child_ids_by_chunk = {str(task.payload["chunk_id"]): task.id for task in children}
    first = await db_session.get(Task, child_ids_by_chunk[first_chunk_id])
    first = (await _claim_task_async(db_session, first))[0]
    await _run_handler_and_transition(db_session, first, run_generate_single_handler)
    parent = await db_session.get(Task, parent_id)
    parent = (await _claim_task_async(db_session, parent))[0]
    parent = await _run_handler_and_transition(db_session, parent, run_generate_batch_handler)
    assert parent.status == "queued"
    batch = await db_session.get(GenerationBatch, batch_id)
    assert batch.status == "processing" and batch.summary_json is None
    second = await db_session.get(Task, child_ids_by_chunk[second_chunk_id])
    second = (await _claim_task_async(db_session, second))[0]
    await _run_handler_and_transition(db_session, second, run_generate_single_handler)
    parent = await db_session.get(Task, parent_id)
    parent = (await _claim_task_async(db_session, parent))[0]
    parent = await _run_handler_and_transition(db_session, parent, run_generate_batch_handler)
    assert parent.status == "failed"
    batch = await db_session.get(GenerationBatch, batch_id)
    assert batch.summary_json["succeeded"] == 1 and batch.summary_json["failed"] == 1
    assert batch.completed_chunks == 1


async def test_set_default_refreshes_cached_target_after_other_writer(db_session, org, _test_session_factory):
    pid = org["projects"]["a"].id
    first = TaskPolicy(project_id=pid, name="first", task_type="parse", is_default=True)
    second = TaskPolicy(project_id=pid, name="second", task_type="parse")
    db_session.add_all([first, second])
    await db_session.commit()
    async with _test_session_factory() as other:
        cached = await other.get(TaskPolicy, first.id)
        assert cached.is_default
        await ConfigService(db_session, TaskPolicy).set_default(pid, second.id)
        await db_session.commit()
        chosen = await ConfigService(other, TaskPolicy).set_default(pid, first.id)
        await other.commit()
        assert chosen.is_default
    ids = (await db_session.execute(select(TaskPolicy.id).where(
        TaskPolicy.project_id == pid, TaskPolicy.task_type == "parse", TaskPolicy.is_default.is_(True),
    ))).scalars().all()
    assert ids == [first.id]


async def test_task_policy_rejects_unknown_task_type(client, org):
    token = await _login(client, "admin_user")
    response = await client.post(
        f"/api/projects/{org['projects']['a'].id}/task-policies/",
        headers=_bearer(token["access_token"]),
        json={"name": "bad", "task_type": "unknown", "max_retries": 0,
              "timeout_seconds": 1, "concurrency_limit": 1},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("stage", ["during_llm", "during_publish"])
async def test_parent_cancel_publication_lock_order_and_final_summary(
    db_session, org, _test_session_factory, monkeypatch, stage,
):
    from app.models.generation import Candidate
    from app.models.generation_batch import GenerationBatch
    from app.workers import generate_worker
    from tests.integration.test_generation_flow import (
        FakeLLM,
        _build_generation_doc,
        _claim_task_async,
        _create_batch,
    )
    entered, release = asyncio.Event(), asyncio.Event()
    res = await _build_generation_doc(db_session, org, chunk_count=2)
    FakeLLM({0: '{"question":"Q0","answer":"A0"}', 1: '{"question":"Q1","answer":"A1"}'}).install(monkeypatch)
    if stage == "during_llm":
        original = generate_worker.LLMClient.chat_completion
        async def gate(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return await original(self, *args, **kwargs)
        monkeypatch.setattr(generate_worker.LLMClient, "chat_completion", gate)
    else:
        original = generate_worker._increment_batch_completed
        async def gate(*args):
            await original(*args)
            entered.set()
            await release.wait()
        monkeypatch.setattr(generate_worker, "_increment_batch_completed", gate)
    batch, parent = await _create_batch(db_session, org=org, doc=res["doc"], tpl=res["tpl"],
                                         model=res["model"], selected=None)
    parent_id, batch_id, uid = parent.id, batch.id, org["users"]["admin"].id
    await db_session.commit()
    children = (await db_session.execute(select(Task).where(
        Task.parent_task_id == parent_id).order_by(Task.created_at))).scalars().all()
    child = (await _claim_task_async(db_session, children[0]))[0]
    await db_session.commit()
    registry = HandlerRegistry()
    registry.register("generate_single", 1)(generate_worker.run_generate_single_handler)
    worker = TaskRunner(_test_session_factory, registry)
    execution = asyncio.create_task(worker._execute_one(child))
    async def cancel():
        async with _test_session_factory() as control:
            task = await TaskService(control).cancel_task(parent_id, uid)
            await control.commit()
            assert task.status == "cancelled"
    cancellation = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        cancellation = asyncio.create_task(cancel())
        if stage == "during_llm":
            await asyncio.wait_for(cancellation, 5)
            async with _test_session_factory() as observer:
                cancelled = await observer.get(GenerationBatch, batch_id)
                assert cancelled.summary_json is None
                cancelled_at = cancelled.completed_at
        else:
            # Publication is deliberately paused holding Batch. Cancellation must use
            # the same Task→Batch order and complete when this short transaction exits.
            await asyncio.sleep(0.05)
        release.set()
        await asyncio.wait_for(asyncio.gather(execution, cancellation), 10)
    finally:
        release.set()
        if cancellation is not None:
            await asyncio.gather(execution, cancellation, return_exceptions=True)
        else:
            await execution
    async with _test_session_factory() as observer:
        final = await observer.get(GenerationBatch, batch_id)
        candidates = (await observer.execute(select(Candidate.id).where(
            Candidate.source_generation_batch_id == batch_id))).scalars().all()
        assert final.status == "cancelled"
        assert final.summary_json["cancelled"] == (2 if stage == "during_llm" else 1)
        assert final.summary_json["succeeded"] == (0 if stage == "during_llm" else 1)
        assert len(candidates) == (0 if stage == "during_llm" else 1)
        if stage == "during_llm":
            assert final.completed_at == cancelled_at
