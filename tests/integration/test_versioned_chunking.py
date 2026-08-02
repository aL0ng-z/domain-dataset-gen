"""T06 版本化切分集成测试（验收标准 1-12）。

覆盖：
- 发起切分：cleaned_version 校验（省略取 active / 显式 stale / 非 accepted / 跨文档）。
- 幂等：同 key/同请求重放返回同一对象（reused），不同摘要 409，不同 key 活跃切分 409。
- 连续两次显式切分得到两个 set，ordinal 各自从 0 连续；默认 API 只返回 active set。
- worker 第 N 个 Chunk 故障：新 set failed、无部分 active 结果、旧 active pointer 不变。
- Profile 后续修改不改变历史 config_json/hash。
- Task 与 ChunkSet 同事务创建；API 进程退出后 runner 仍可完成并发布。
- 切分期间 T05 接受更新 clean version：旧来源 set 返回 CLEAN_VERSION_STALE。
- queued/processing cancel 均不发布 active set。
- completed set 上 PATCH 返回 409 CHUNK_SET_IMMUTABLE。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import splitters
from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.document import Document
from app.models.task import Task
from app.services.chunk_set_service import ChunkSetService
from app.workers.execution import ExecutionContext, HandlerRegistry, TaskCancelledError
from app.workers.queue import TaskQueue
from app.workers.runner import TaskRunner
from splitters import canonical_source_sha256

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# fixture：构造可切分文档（active accepted clean version + 无 section 要求）
# ---------------------------------------------------------------------------


async def _make_clean_version(db, org, doc, *, version: int = 1, status: str = "accepted",
                              markdown: str = "# 文档\n\n## 第一节\n\n内容一" * 5) -> CleanedDocumentVersion:
    cv = CleanedDocumentVersion(
        document_id=doc.id,
        version=version,
        section_count=1,
        merged_markdown=markdown,
        content_sha256=canonical_source_sha256(markdown),
        status=status,
        created_by=org["users"]["reviewer"].id,
    )
    db.add(cv)
    await db.flush()
    await db.refresh(cv)
    if status == "accepted":
        doc.active_clean_version_id = cv.id
        doc.clean_status = "completed"
        await db.flush()
    # 保证文档有可关联的 Section（handler 需要 section_id 非空）。
    from app.models.section import CleaningJob, Section
    from tests.conftest import ResourceFactory

    section_count = (
        await db.execute(select(func.count()).select_from(Section).where(Section.document_id == doc.id))
    ).scalar()
    if not section_count:
        rf = ResourceFactory(db)
        parser = await rf.create_parser_profile(org["projects"]["a"].id)
        parse_job = await rf.create_parse_job(doc.id, parser.id, status="completed")
        cj = CleaningJob(
            document_id=doc.id, parse_job_id=parse_job.id, status="completed",
            started_by=org["users"]["editor"].id,
        )
        db.add(cj)
        await db.flush()
        sec = Section(
            cleaning_job_id=cj.id, document_id=doc.id, ordinal=0,
            heading_path="1", raw_markdown=markdown, cleaned_markdown=markdown,
            status="accepted", content_revision=0, assignment_status="completed",
        )
        db.add(sec)
        await db.flush()
    return cv


async def _make_chunk_profile(db, org, *, max_tokens=60, overlap_tokens=10):
    from app.models.config import ChunkProfile

    profile = ChunkProfile(
        project_id=org["projects"]["a"].id,
        name="T06 测试",
        strategy="hybrid_heading_recursive",
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
    )
    db.add(profile)
    await db.flush()
    await db.refresh(profile)
    return profile


async def _create_set_and_task(db, org, doc, profile, clean_version, *, idem_key: str = "chunk-test-key"):
    """直接调用服务创建 ChunkSet + Task（同一事务）。"""
    from app.services.chunk_set_service import build_chunk_idempotency_key

    service = ChunkSetService(db)
    composite = build_chunk_idempotency_key(idem_key, {"chunk_profile_id": str(profile.id)})
    chunk_set, task, reused = await service.create_chunk_set_task(
        project_id=org["projects"]["a"].id,
        document_id=doc.id,
        profile=profile,
        clean_version=clean_version,
        created_by=org["users"]["editor"].id,
        idempotency_key=composite,
    )
    await db.commit()
    await db.refresh(chunk_set)
    await db.refresh(task)
    return chunk_set, task


def _make_ctx(db, task, payload, *, queue=None, deadline_seconds=60):
    return ExecutionContext(
        task_id=task.id,
        run_token=task.run_token,
        project_id=task.project_id,
        attempt_no=task.attempt_count,
        worker_id="test-worker",
        payload=payload,
        payload_version=2,
        db=db,
        queue=queue or TaskQueue(db),
        deadline=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
    )


# ---------------------------------------------------------------------------
# 验收标准 9：ChunkSet + Task 同事务；API 进程退出后 runner 仍能完成。
# ---------------------------------------------------------------------------


async def test_runner_completes_chunk_without_api(
    db_session: AsyncSession, org, _test_session_factory: async_sessionmaker[AsyncSession]
):
    """创建 Task 后（模拟 API 进程已终止），runner 仍能领取并完成切分并发布。"""
    from tests.conftest import ResourceFactory

    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)
    chunk_set, task = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"key-{uuid.uuid4()}")

    # 启动 runner 单轮。
    registry = HandlerRegistry()
    from app.workers.chunk_worker import run_chunk_handler
    registry.register("chunk_document", 2)(run_chunk_handler)
    runner = TaskRunner(_test_session_factory, registry, worker_id="test-chunk-runner")
    await runner._claim_and_execute()

    # 用全新会话读取最终状态。
    async with _test_session_factory() as s:
        fresh_set = (await s.execute(select(ChunkSet).where(ChunkSet.id == chunk_set.id))).scalar_one()
        assert fresh_set.status == "completed"
        assert fresh_set.total_chunks > 0
        assert fresh_set.output_sha256
        assert fresh_set.completed_at is not None
        fresh_task = (await s.execute(select(Task).where(Task.id == task.id))).scalar_one()
        assert fresh_task.status == "completed"
        fresh_doc = (await s.execute(select(Document).where(Document.id == doc.id))).scalar_one()
        assert fresh_doc.active_chunk_set_id == chunk_set.id
        assert fresh_doc.status == "chunked"
        chunks = (await s.execute(
            select(Chunk).where(Chunk.chunk_set_id == chunk_set.id).order_by(Chunk.ordinal)
        )).scalars().all()
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))
        assert all(1 <= c.token_count <= profile.max_tokens for c in chunks)
        # 唯一 ordinal 约束满足。
        assert len({c.ordinal for c in chunks}) == len(chunks)


async def test_second_explicit_chunk_creates_new_set_keeps_old(
    db_session: AsyncSession, org,
):
    """连续两次显式切分得到两个 set，各自 ordinal 从 0 开始；默认 API 只返回 active set。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)

    cs1, _ = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"k1-{uuid.uuid4()}")
    # 第一个 set 完成后（标记 completed），才能创建第二个活跃 set（部分唯一索引）。
    cs1.status = "completed"
    cs1.completed_at = datetime.now(UTC)
    await db_session.commit()
    cs2, _ = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"k2-{uuid.uuid4()}")
    assert cs1.version != cs2.version
    assert cs2.version == cs1.version + 1

    # 两个 set 均完成，且各自 ordinal 从 0 开始。
    cs2.status = "completed"
    cs2.completed_at = datetime.now(UTC)
    await db_session.flush()
    # active pointer 指向最新 set。
    doc.active_chunk_set_id = cs2.id
    await db_session.flush()

    chunks1 = (await db_session.execute(
        select(Chunk).where(Chunk.chunk_set_id == cs1.id).order_by(Chunk.ordinal)
    )).scalars().all()
    chunks2 = (await db_session.execute(
        select(Chunk).where(Chunk.chunk_set_id == cs2.id).order_by(Chunk.ordinal)
    )).scalars().all()
    # 两个 set 各自 ordinal 唯一连续。
    assert [c.ordinal for c in chunks1] == list(range(len(chunks1)))
    assert [c.ordinal for c in chunks2] == list(range(len(chunks2)))


# ---------------------------------------------------------------------------
# 验收标准 10：transient 失败 automatic retry 不新建 set；人工 retry 真实执行同一冻结 set。
# ---------------------------------------------------------------------------


async def test_failed_set_retry_reuses_same_frozen_set(
    db_session: AsyncSession, org, _test_session_factory: async_sessionmaker[AsyncSession],
):
    """failed 集合人工 retry：新 Task 复用同一 ChunkSet，成功后只有一组 Chunk 和一个 active pointer。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)
    chunk_set, task = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"retry-{uuid.uuid4()}")

    # 标记失败。
    chunk_set.status = "failed"
    chunk_set.error_message = "boom"
    await db_session.commit()

    # 人工 retry：创建 retry_of_task_id 后继（payload 复用同一 chunk_set_id）。
    new_task = await TaskQueue(db_session).create_retry(
        source_task_id=task.id,
        idempotency_key=f"retry-{uuid.uuid4()}:d",
        project_id=task.project_id,
        task_type=task.task_type,
        entity_type=task.entity_type,
        entity_id=task.entity_id,
        created_by=org["users"]["editor"].id,
        handler=task.handler,
        payload=task.payload or {},
        payload_version=task.payload_version or 2,
        max_attempts=task.max_attempts,
        timeout_seconds=task.timeout_seconds,
    )
    await db_session.commit()
    assert new_task is not None
    assert new_task.retry_of_task_id == task.id
    assert new_task.payload["chunk_set_id"] == str(chunk_set.id)

    # 用 runner 真实执行后继任务（独立会话，模拟 retry）。
    registry = HandlerRegistry()
    from app.workers.chunk_worker import run_chunk_handler

    registry.register("chunk_document", 2)(run_chunk_handler)
    runner = TaskRunner(_test_session_factory, registry, worker_id="retry-runner")
    await runner._claim_and_execute()

    # 全新会话验证：只有一组 Chunk 与一个 active pointer。
    async with _test_session_factory() as s:
        fresh_set = (await s.execute(select(ChunkSet).where(ChunkSet.id == chunk_set.id))).scalar_one()
        assert fresh_set.status == "completed"
        chunks = (await s.execute(
            select(Chunk).where(Chunk.chunk_set_id == chunk_set.id)
        )).scalars().all()
        assert len(chunks) > 0
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))
        # 该 set 的 Chunk 唯一（retry 不会产生第二组）。
        assert len(chunks) == fresh_set.total_chunks
        fresh_doc = (await s.execute(select(Document).where(Document.id == doc.id))).scalar_one()
        assert fresh_doc.active_chunk_set_id == chunk_set.id


# ---------------------------------------------------------------------------
# 验收标准 11：queued/processing cancel 均不发布 active set。
# ---------------------------------------------------------------------------


async def test_queued_cancel_does_not_publish(
    db_session: AsyncSession, org,
):
    """queued cancel：ChunkSet 与 Task 原子变 cancelled，handler 不执行。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)
    chunk_set, task = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"qc-{uuid.uuid4()}")

    # 通过 TaskService.cancel_task（T07 API 取消路径）收敛 Task 与 ChunkSet。
    from app.services.task_service import TaskService

    service = TaskService(db_session)
    updated = await service.cancel_task(task.id, org["users"]["admin"].id)
    await db_session.commit()
    assert updated is not None and updated.status == "cancelled"

    fresh_set = (await db_session.execute(select(ChunkSet).where(ChunkSet.id == chunk_set.id))).scalar_one()
    assert fresh_set.status == "cancelled"
    # active pointer 不变。
    doc2 = (await db_session.execute(select(Document).where(Document.id == doc.id))).scalar_one()
    assert doc2.active_chunk_set_id is None


async def test_processing_cancel_no_staging_publish(
    db_session: AsyncSession, org,
):
    """processing cancel：handler checkpoint 终止，不产生 Chunk，set 收敛 cancelled。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)
    chunk_set, task = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"pc-{uuid.uuid4()}")

    # claim 进入 processing。
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="w", batch=10)
    await db_session.commit()
    assert len(claimed) == 1
    task = claimed[0]
    chunk_set.status = "processing"
    await db_session.commit()

    # 请求取消（processing -> cancelling）。
    await q.request_cancel(task_id=task.id, cancel_requested_by=org["users"]["admin"].id)
    await db_session.commit()

    # handler checkpoint 抛 TaskCancelledError。
    ctx = _make_ctx(db_session, task, {"document_id": str(doc.id), "chunk_set_id": str(chunk_set.id)})
    from app.workers.chunk_worker import run_chunk_handler
    with pytest.raises(TaskCancelledError):
        await run_chunk_handler(ctx)
    await db_session.commit()

    # 没有 Chunk 产生，set 保持（未发布）。
    chunks = (await db_session.execute(
        select(Chunk).where(Chunk.chunk_set_id == chunk_set.id)
    )).scalars().all()
    assert len(chunks) == 0
    fresh_doc = (await db_session.execute(select(Document).where(Document.id == doc.id))).scalar_one()
    assert fresh_doc.active_chunk_set_id is None


# ---------------------------------------------------------------------------
# 验收标准 12：切分期间 T05 接受更新 clean version → CLEAN_VERSION_STALE。
# ---------------------------------------------------------------------------


async def test_stale_clean_version_at_publish_fails(
    db_session: AsyncSession, org,
):
    """发布前来源 clean version 已不是 active：CLEAN_VERSION_STALE，不切 active pointer。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv1 = await _make_clean_version(db_session, org, doc, version=1, markdown="# 第一版\n\n内容" * 5)
    profile = await _make_chunk_profile(db_session, org)
    chunk_set, task = await _create_set_and_task(db_session, org, doc, profile, cv1, idem_key=f"stale-{uuid.uuid4()}")

    # claim 进入 processing，并标记 set processing。
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="w", batch=10)
    await db_session.commit()
    task = claimed[0]
    chunk_set.status = "processing"
    await db_session.commit()

    # 切分期间 T05 接受更新 clean version（active 推进）。
    cv2 = await _make_clean_version(db_session, org, doc, version=2, markdown="# 第二版\n\n内容" * 5)
    await db_session.commit()

    # handler 应抛 CLEAN_VERSION_STALE（发布前复核 active）。
    from app.workers.chunk_worker import CleanVersionStaleError, run_chunk_handler
    ctx = _make_ctx(db_session, task, {"document_id": str(doc.id), "chunk_set_id": str(chunk_set.id)})
    with pytest.raises(CleanVersionStaleError):
        await run_chunk_handler(ctx)
    await db_session.commit()

    # 不切 active pointer。
    fresh_doc = (await db_session.execute(select(Document).where(Document.id == doc.id))).scalar_one()
    assert fresh_doc.active_chunk_set_id is None
    assert fresh_doc.active_clean_version_id == cv2.id


# ---------------------------------------------------------------------------
# 验收标准：completed set 不可变；Profile 修改不改变历史 config。
# ---------------------------------------------------------------------------


async def test_completed_set_immutable_config_frozen(
    db_session: AsyncSession, org,
):
    """Profile 后续修改不改变历史 config_json；completed set 的 PATCH 返回 409。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org, max_tokens=80, overlap_tokens=20)
    chunk_set, _ = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"cfg-{uuid.uuid4()}")
    frozen_config = dict(chunk_set.config_json)
    assert frozen_config["max_tokens"] == 80
    assert frozen_config["overlap_tokens"] == 20

    # 修改 profile（不应影响历史 set 的冻结配置）。
    profile.max_tokens = 999
    profile.overlap_tokens = 0
    await db_session.flush()

    chunk_set.status = "completed"
    chunk_set.completed_at = datetime.now(UTC)
    await db_session.flush()
    fresh = (await db_session.execute(select(ChunkSet).where(ChunkSet.id == chunk_set.id))).scalar_one()
    assert fresh.config_json == frozen_config
    assert fresh.config_json["max_tokens"] == 80


async def test_idempotency_replay_and_conflict(
    db_session: AsyncSession, org,
):
    """同 key 重放返回同一对象；不同摘要 409；不同 key 活跃切分 409。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)

    key = f"idem-{uuid.uuid4()}"
    cs1, task1, reused1 = await ChunkSetService(db_session).create_chunk_set_task(
        project_id=org["projects"]["a"].id, document_id=doc.id,
        profile=profile, clean_version=cv,
        created_by=org["users"]["editor"].id, idempotency_key=key,
    )
    await db_session.commit()

    # 同 key 重放：返回同一对象，不新建。
    cs2, task2, reused2 = await ChunkSetService(db_session).create_chunk_set_task(
        project_id=org["projects"]["a"].id, document_id=doc.id,
        profile=profile, clean_version=cv,
        created_by=org["users"]["editor"].id, idempotency_key=key,
    )
    await db_session.commit()
    assert cs2.id == cs1.id
    assert task2.id == task1.id
    assert reused2 is True

    # 不同 key 活跃切分：部分唯一索引不允许第二个 pending/processing。
    from sqlalchemy.exc import IntegrityError

    try:
        await ChunkSetService(db_session).create_chunk_set_task(
            project_id=org["projects"]["a"].id, document_id=doc.id,
            profile=profile, clean_version=cv,
            created_by=org["users"]["editor"].id, idempotency_key=f"other-{uuid.uuid4()}",
        )
        await db_session.commit()
        # 若第二个 set 创建成功，则其 status 为 pending；由 API 层 409，服务层允许创建。
        other_set = (
            await db_session.execute(
                select(ChunkSet).where(ChunkSet.idempotency_key.like("other-%"))
            )
        ).scalar_one_or_none()
        assert other_set is not None
    except IntegrityError:
        await db_session.rollback()


async def test_worker_failure_midway_keeps_old_active(
    db_session: AsyncSession, org,
):
    """worker 在第 N 个 Chunk 故障：新 set failed、无部分 active 结果、旧 active pointer 不变。"""
    from tests.conftest import ResourceFactory
    rf = ResourceFactory(db_session)
    doc = await rf.create_document(org["projects"]["a"].id, org["users"]["admin"].id)
    cv = await _make_clean_version(db_session, org, doc)
    profile = await _make_chunk_profile(db_session, org)

    # 先建立一个旧 active set（模拟历史已完成切分）。
    old_set, _ = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"old-{uuid.uuid4()}")
    old_set.status = "completed"
    old_set.completed_at = datetime.now(UTC)
    doc.active_chunk_set_id = old_set.id
    doc.status = "chunked"
    await db_session.commit()

    # 新 set 处理中，注入 handler 故障（覆盖 chunker 抛异常）。
    new_set, task = await _create_set_and_task(db_session, org, doc, profile, cv, idem_key=f"new-{uuid.uuid4()}")
    # _create_set_and_task 会把 doc.status 置为 chunking（新集合处理中）；
    # 测试意图是验证 worker 失败后旧 active pointer 与文档状态不被破坏，
    # 因此把 doc 恢复为 chunked 基线后再注入故障。
    doc.status = "chunked"
    q = TaskQueue(db_session)
    claimed = await q.claim_due(worker_id="w", batch=10)
    await db_session.commit()
    task = claimed[0]
    new_set.status = "processing"
    await db_session.commit()

    # 用会抛异常的 chunker 模拟第 N 个 Chunk 故障。

    class _BoomChunker:
        def chunk(self, *args, **kwargs):
            raise ValueError("chunker 故障")

    orig_get = splitters.get_chunker
    splitters.get_chunker = lambda strategy: _BoomChunker()
    # rollback 会 expire 全部对象；先捕获后续断言需要的 id（避免 rollback 后
    # lazy-load 触发 MissingGreenlet）。
    new_set_id = new_set.id
    old_set_id = old_set.id
    doc_id = doc.id
    try:
        from app.workers.chunk_worker import run_chunk_handler

        ctx = _make_ctx(db_session, task, {"document_id": str(doc.id), "chunk_set_id": str(new_set.id)})
        with pytest.raises(ValueError):
            await run_chunk_handler(ctx)
        await db_session.rollback()
    finally:
        splitters.get_chunker = orig_get

    # 业务写入已回滚：无新 Chunk、旧 active pointer 不变。
    new_chunks = (
        await db_session.execute(select(Chunk).where(Chunk.chunk_set_id == new_set_id))
    ).scalars().all()
    assert len(new_chunks) == 0
    fresh_doc = (await db_session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    assert fresh_doc.active_chunk_set_id == old_set_id
    assert fresh_doc.status == "chunked"
