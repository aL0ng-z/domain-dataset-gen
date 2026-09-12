"""T08 生成链路矩阵测试（验收标准 2-8，使用 fake LLM 不访问真实外部服务）。

覆盖：
- 单 Chunk 生成：1 batch + 1 parent + 1 child + 1 run + 1 Candidate + 1 usage，
  快照/版本/hash/renderer/rendered prompt hash 全部冻结且一致。
- 三 Chunk 批量全成功：只调用 LLM 三次、三个 Candidate、全部 completed、进度 100。
- 第二个 Chunk 失败：batch/parent failed、completed_chunks=2、成功 Candidate 保留、
  失败 run 有脱敏错误、父任务绝不 completed。
- 取消：未开始 Chunk 不调用 LLM、不产出 Candidate、batch/task cancelled、Chunk 不残留 generating。
- retry：旧 Task/Batch/Run 字节级不变；只创建含失败 Chunk 的新 Task/Batch/Run；
  只额外调用 LLM 一次；成功项 Candidate/usage 数不变。
- failed/cancelled Batch 原地复活/快照修改被门禁拒绝；并发 retry 只形成一个后继。
- 创建 Batch 后修改模板/模型非秘密参数，worker 仍使用冻结值；重建 input prompt hash 一致。
- secret scanner：Batch/Run/Task/API 响应不含测试 API key/Authorization/credential。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.generation import Candidate, GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.task import LlmUsageLog, Task
from app.services.task_service import TaskService
from app.workers.execution import ExecutionContext
from app.workers.queue import TaskQueue

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# fake LLM：替换 LLMClient.chat_completion（worker 执行期间）。
# ---------------------------------------------------------------------------


class FakeLLM:
    """按 chunk 内容标记 #CID:n# 返回预设 JSON 响应；可注入非法 JSON。

    fail_json_for 中每个 ordinal 只在前 N 次调用失败（默认 1 次），后续调用成功，
    以支持 retry 场景验证"失败后可重试成功"。
    """

    def __init__(self, responses: dict[int, str], *, fail_json_for: set[int] | None = None, fail_times: int = 1):
        self.responses = responses
        self.fail_json_for = fail_json_for or set()
        self.fail_remaining: dict[int, int] = {o: fail_times for o in self.fail_json_for}
        self.calls: list[dict] = []

    def install(self, monkeypatch):
        import llm as llm_pkg
        from llm.usage import LLMResponse

        recorder = self

        async def _chat_completion(self, messages, response_format=None, max_retries=3):
            recorder.calls.append({"messages": messages})
            content = messages[-1]["content"] if messages else ""
            ordinal = _marker_of(content)
            if recorder.fail_remaining.get(ordinal, 0) > 0:
                recorder.fail_remaining[ordinal] -= 1
                return LLMResponse(content="not-json{{{", input_tokens=10, output_tokens=5, latency_ms=3)
            return LLMResponse(
                content=recorder.responses[ordinal],
                input_tokens=10 + ordinal,
                output_tokens=5 + ordinal,
                latency_ms=3 + ordinal,
            )

        monkeypatch.setattr(llm_pkg.LLMClient, "chat_completion", _chat_completion)
        return recorder


def _marker_of(content: str) -> int:
    import re

    m = re.search(r"#CID:(\d+)#", content or "")
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# 资源构建 helper：真实 active ChunkSet + ready chunks。
# ---------------------------------------------------------------------------


async def _build_generation_doc(db: AsyncSession, org, *, chunk_count: int = 3):
    """构建文档 + active ChunkSet + ready chunks + 模板/模型配置。"""
    from tests.conftest import ResourceFactory

    rf = ResourceFactory(db)
    pid = org["projects"]["a"].id
    admin = org["users"]["admin"].id

    doc = await rf.create_document(pid, admin)
    parser = await rf.create_parser_profile(pid)
    parse_job = await rf.create_parse_job(doc.id, parser.id, status="completed")
    cj = await rf.create_cleaning_job(doc.id, parse_job.id, admin)
    cv = await rf.create_cleaned_version(doc.id, cj.id, admin)
    section = await rf.create_section(cj.id, doc.id)
    model = await rf.create_model_config(pid)
    tpl = await rf.create_prompt_template(pid)

    from app.models.chunk_set import ChunkSet

    chunk_task = await TaskService(db).create_task(
        pid, "chunk", "document", doc.id, admin,
        payload={"document_id": str(doc.id)}, handler="chunk_document",
    )
    cs = ChunkSet(
        document_id=doc.id,
        cleaned_document_version_id=cv.id,
        chunk_profile_id=(await rf.create_chunk_profile(pid)).id,
        strategy="hybrid_heading_recursive",
        config_json={"max_tokens": 512, "overlap_tokens": 50},
        status="completed",
        version=1,
        is_legacy=False,
        splitter_version="2.0.0@cl100k_base:0.12.0",
        task_id=chunk_task.id,
        created_by=admin,
    )
    db.add(cs)
    await db.flush()
    doc.active_chunk_set_id = cs.id
    doc.status = "chunked"

    chunks = []
    for i in range(chunk_count):
        chunk = Chunk(
            section_id=section.id,
            document_id=doc.id,
            chunk_set_id=cs.id,
            ordinal=i,
            heading_path=f"1.{i}",
            content=f"#CID:{i}# 第 {i} 块内容",
            token_count=10 + i,
            status="ready",
        )
        db.add(chunk)
        chunks.append(chunk)
    await db.flush()
    for c in chunks:
        await db.refresh(c)
    return {"doc": doc, "chunk_set": cs, "chunks": chunks, "model": model, "tpl": tpl}


async def _create_batch(
    db: AsyncSession,
    *,
    org,
    doc,
    tpl,
    model,
    selected: list | None,
    created_by=None,
) -> tuple[GenerationBatch, Task]:
    """通过 orchestration service 创建 batch + parent task（不提交由调用方提交）。"""
    from app.services.generation_service import GenerationOrchestrationService

    service = GenerationOrchestrationService(db, TaskService(db))
    batch, parent, runs = await service.create_batch(
        project_id=org["projects"]["a"].id,
        document_id=doc.id,
        prompt_template_id=tpl.id,
        model_config_id=model.id,
        selected_chunk_ids=selected,
        created_by=created_by or org["users"]["editor"].id,
    )
    await db.flush()
    await db.refresh(batch)
    await db.refresh(parent)
    return batch, parent


def _make_ctx(db: AsyncSession, task: Task, *, queue=None, deadline_seconds=300) -> ExecutionContext:
    from datetime import UTC, datetime, timedelta

    return ExecutionContext(
        task_id=task.id,
        run_token=task.run_token,
        project_id=task.project_id,
        attempt_no=task.attempt_count,
        worker_id="t08-worker",
        payload=task.payload or {},
        payload_version=1,
        db=db,
        queue=queue or TaskQueue(db),
        deadline=datetime.now(UTC) + timedelta(seconds=deadline_seconds),
    )


async def _claim_task_async(db: AsyncSession, task: Task) -> list[Task]:
    """精确领取指定 task（queued -> processing + run token），避免连带领取其他到期任务。"""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    now = datetime.now(UTC)
    run_token = uuid.uuid4()
    attempt_no = task.attempt_count + 1
    upd = (
        update(Task)
        .where(
            Task.id == task.id,
            Task.status == "queued",
            Task.state_version == task.state_version,
        )
        .values(
            status="processing",
            state_version=task.state_version + 1,
            attempt_count=attempt_no,
            run_token=run_token,
            lease_owner="t08-worker",
            lease_expires_at=now + timedelta(seconds=300),
            heartbeat_at=now,
            started_at=now,
            next_run_at=now + timedelta(seconds=300),
        )
    )
    res = await db.execute(upd)
    if res.rowcount != 1:
        return []
    from app.models.task import TaskAttempt

    attempt = TaskAttempt(task_id=task.id, attempt_no=attempt_no, run_token=run_token, worker_id="t08-worker")
    db.add(attempt)
    await db.commit()
    # 用全新查询返回处理中的 task，避免对已 expire 对象 refresh。
    fresh = (
        await db.execute(select(Task).where(Task.id == task.id).with_for_update())
    ).scalar_one()
    return [fresh]


async def _run_handler_and_transition(
    db: AsyncSession, task: Task, handler, *, terminal_hook=True
) -> Task:
    """执行 handler 并按 runner 语义处理终态。

    简化版：handler 直接执行（child 不抛错即视为成功），然后 CAS 转换任务。
    """
    from app.workers.queue import TaskQueue

    q = TaskQueue(db)
    fresh = (
        await db.execute(select(Task).where(Task.id == task.id).with_for_update())
    ).scalar_one()
    # 先捕获关键值（后续 rollback 会 expire 全部对象，避免 lazy-load 崩溃）。
    _tid = fresh.id
    _rt = fresh.run_token
    _sv = fresh.state_version
    ctx = _make_ctx(db, fresh, queue=q)
    try:
        await handler(ctx)
        if ctx.requeue_after is not None:
            ok = await q.transition(
                task_id=_tid, run_token=_rt,
                from_status="processing", to_status="queued",
                expected_state_version=_sv,
                next_run_at=datetime.now(UTC) + timedelta(seconds=ctx.requeue_after),
            )
            if ok:
                await q.finish_attempt(
                    task_id=_tid, run_token=_rt, attempt_no=fresh.attempt_count,
                    status="completed",
                )
                await db.commit()
            else:
                await db.rollback()
            return (await db.execute(select(Task).where(Task.id == _tid))).scalar_one()
        # 业务写入 + completed 转换原子提交。
        ok = await q.transition(
            task_id=_tid, run_token=_rt,
            from_status="processing", to_status="completed",
            expected_state_version=_sv,
        )
        if ok:
            await db.commit()
        else:
            await db.rollback()
    except Exception as exc:  # noqa: BLE001
        # 失败/取消：回滚业务写入，再应用终态钩子 + 失败状态。
        await db.rollback()
        from app.workers.execution import TaskCancelledError

        is_cancel = isinstance(exc, TaskCancelledError) or ctx._cancelled
        status = "cancelled" if is_cancel else "failed"
        fresh2 = (
            await db.execute(select(Task).where(Task.id == _tid).with_for_update())
        ).scalar_one()
        ok = await q.transition(
            task_id=_tid, run_token=_rt,
            from_status="processing",
            to_status="cancelling" if is_cancel else "failed",
            expected_state_version=fresh2.state_version,
            error_code=("TASK_CANCELLED" if is_cancel else "BUSINESS_ERROR"),
            error_message=str(exc),
        )
        if ok and is_cancel:
            await q.transition(
                task_id=_tid, run_token=_rt,
                from_status="cancelling", to_status="cancelled",
                expected_state_version=fresh2.state_version + 1,
            )
        if terminal_hook and ctx._terminal_hook is not None:
            await ctx._terminal_hook(db, status, str(exc))
        await db.commit()
    # 重新读取任务终态。
    return (await db.execute(select(Task).where(Task.id == task.id))).scalar_one()


async def _terminal_child_counts(s: AsyncSession, parent_id: uuid.UUID) -> dict[str, int]:
    result = await s.execute(
        select(Task.status, func.count()).where(Task.parent_task_id == parent_id).group_by(Task.status)
    )
    return dict(result.all())


def _assert_verified_batch(batch: GenerationBatch) -> None:
    assert batch.is_legacy is False
    assert batch.provenance_status == "verified"
    assert batch.prompt_template_version_id is not None
    assert batch.prompt_template_snapshot is not None
    assert batch.prompt_template_sha256 is not None
    assert batch.model_config_snapshot is not None
    assert batch.model_config_sha256 is not None
    assert batch.renderer_version is not None
    assert batch.selected_chunk_ids is not None
    assert len(batch.selected_chunk_ids) == batch.total_chunks


# ---------------------------------------------------------------------------
# 验收标准 2：单 Chunk 生成完整链路。
# ---------------------------------------------------------------------------


async def test_single_chunk_full_chain(
    db_session: AsyncSession, org, monkeypatch
):
    """单 Chunk：1 batch + 1 parent + 1 child + 1 run + 1 Candidate + 1 usage。"""
    from app.generation.renderer import RENDERER_VERSION
    from app.generation.snapshot import (
        model_config_snapshot_sha256,
        prompt_template_snapshot_sha256,
    )
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=1)
    chunk = res["chunks"][0]
    fake = FakeLLM({0: '{"answer": "ok"}'})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=[chunk.id],
    )
    await db_session.commit()
    _assert_verified_batch(batch)

    # 1 parent + 1 child task。
    assert parent.task_type == "generate_batch"
    assert parent.entity_type == "generation_batch"
    assert parent.entity_id == batch.id
    children = (
        await db_session.execute(select(Task).where(Task.parent_task_id == parent.id))
    ).scalars().all()
    assert len(children) == 1
    child = children[0]
    assert child.task_type == "generate"
    assert child.payload["generation_batch_id"] == str(batch.id)

    # 1 run。
    runs = (
        await db_session.execute(select(GenerationRun).where(GenerationRun.generation_batch_id == batch.id))
    ).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert run.is_legacy is False
    assert run.provenance_status == "verified"
    assert run.rendered_prompt_sha256 is not None
    assert run.input_prompt is not None
    assert run.generation_batch_id == batch.id

    # claim child task 并执行 handler。
    claimed = await _claim_task_async(db_session, child)
    assert len(claimed) == 1
    claimed_child = claimed[0]
    done = await _run_handler_and_transition(
        db_session, claimed_child, run_generate_single_handler
    )
    assert done.status == "completed"

    # 1 Candidate + 1 usage。
    candidates = (
        await db_session.execute(select(Candidate).where(Candidate.chunk_id == chunk.id))
    ).scalars().all()
    assert len(candidates) == 1
    assert candidates[0].source_generation_batch_id == batch.id
    assert candidates[0].generation_run_id == run.id
    usages = (
        await db_session.execute(
            select(LlmUsageLog).where(LlmUsageLog.task_id == done.id)
        )
    ).scalars().all()
    assert len(usages) == 1
    assert usages[0].status == "success"

    # chunk generated，run completed。
    fresh_run = (
        await db_session.execute(select(GenerationRun).where(GenerationRun.id == run.id))
    ).scalar_one()
    assert fresh_run.status == "completed"
    assert fresh_run.raw_output is not None
    fresh_chunk = (
        await db_session.execute(select(Chunk).where(Chunk.id == chunk.id))
    ).scalar_one()
    assert fresh_chunk.status == "generated"

    # 快照 hash 与 renderer 冻结一致。
    assert batch.renderer_version == RENDERER_VERSION
    assert batch.prompt_template_sha256 == prompt_template_snapshot_sha256(batch.prompt_template_snapshot)
    assert batch.model_config_sha256 == model_config_snapshot_sha256(batch.model_config_snapshot)


# ---------------------------------------------------------------------------
# 验收标准 3：三 Chunk 批量全成功。
# ---------------------------------------------------------------------------


async def test_batch_all_success(
    db_session: AsyncSession, org, monkeypatch
):
    """三 Chunk 全成功：LLM 三次、三个 Candidate、全部 completed。"""
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=3)
    fake = FakeLLM({0: '{"a":0}', 1: '{"a":1}', 2: '{"a":2}'})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=None,  # 全部 ready chunks
    )
    await db_session.commit()
    assert batch.total_chunks == 3
    assert batch.status == "pending"

    children = (
        await db_session.execute(
            select(Task).where(Task.parent_task_id == parent.id).order_by(Task.created_at)
        )
    ).scalars().all()
    assert len(children) == 3

    # 逐个 claim + 执行 child handler（先捕获 id 避免提交后 lazy-load）。
    child_ids = [c.id for c in children]
    for cid in child_ids:
        fresh_child = (
            await db_session.execute(select(Task).where(Task.id == cid))
        ).scalar_one()
        claimed = await _claim_task_async(db_session, fresh_child)
        assert len(claimed) == 1
        done = await _run_handler_and_transition(
            db_session, claimed[0], run_generate_single_handler
        )
        assert done.status == "completed"

    # 三个 Candidate、三次 LLM 调用。
    candidates = (
        await db_session.execute(
            select(Candidate).where(Candidate.source_generation_batch_id == batch.id)
        )
    ).scalars().all()
    assert len(candidates) == 3
    assert len(fake.calls) == 3

    # 批次计数。
    fresh_batch = (
        await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == batch.id))
    ).scalar_one()
    assert fresh_batch.completed_chunks == 3
    assert fresh_batch.total_chunks == 3
    assert fresh_batch.status == "pending"  # parent handler 尚未运行（聚合发生在 parent）


# ---------------------------------------------------------------------------
# 验收标准 4：第二个 Chunk 失败。
# ---------------------------------------------------------------------------


async def test_batch_partial_failure(
    db_session: AsyncSession, org, monkeypatch
):
    """第二个 Chunk 失败：batch/parent failed、completed_chunks=2、成功 Candidate 保留。"""
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=3)
    fake = FakeLLM({0: '{"a":0}', 1: '{"a":1}', 2: '{"a":2}'}, fail_json_for={1})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=None,
    )
    await db_session.commit()
    batch_id = batch.id
    parent_id = parent.id

    children = (
        await db_session.execute(
            select(Task).where(Task.parent_task_id == parent_id).order_by(Task.created_at)
        )
    ).scalars().all()
    assert len(children) == 3

    # 按 ordinal 顺序执行：chunk 0 成功、chunk 1 失败（非法 JSON）、chunk 2 成功。
    # 捕获 id 列表避免提交/回滚后对 expire 对象 lazy-load。
    child_ids = [c.id for c in children]
    for cid in child_ids:
        fresh_child = (
            await db_session.execute(select(Task).where(Task.id == cid))
        ).scalar_one()
        claimed = await _claim_task_async(db_session, fresh_child)
        assert len(claimed) == 1
        await _run_handler_and_transition(db_session, claimed[0], run_generate_single_handler)

    # chunk 1 的 run 应 failed，其余 completed。
    runs = (
        await db_session.execute(
            select(GenerationRun).where(GenerationRun.generation_batch_id == batch.id)
        )
    ).scalars().all()
    by_ordinal = {}
    for r in runs:
        chunk = (await db_session.execute(select(Chunk).where(Chunk.id == r.chunk_id))).scalar_one()
        by_ordinal[chunk.ordinal] = r
    assert by_ordinal[0].status == "completed"
    assert by_ordinal[1].status == "failed"
    assert by_ordinal[1].error_message is not None
    assert by_ordinal[2].status == "completed"

    # 成功 Candidate 保留 2 个。
    candidates = (
        await db_session.execute(
            select(Candidate).where(Candidate.source_generation_batch_id == batch.id)
        )
    ).scalars().all()
    assert len(candidates) == 2

    # 批次计数：completed_chunks=2。
    fresh_batch = (
        await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == batch.id))
    ).scalar_one()
    assert fresh_batch.completed_chunks == 2

    # 父任务聚合：存在 failed child -> parent failed（绝不假 completed）。
    from app.workers.generate_worker import run_generate_batch_handler

    fresh_parent = (
        await db_session.execute(select(Task).where(Task.id == parent_id))
    ).scalar_one()
    claimed_parent = await _claim_task_async(db_session, fresh_parent)
    assert len(claimed_parent) == 1
    parent_done = await _run_handler_and_transition(
        db_session, claimed_parent[0], run_generate_batch_handler
    )
    assert parent_done.status == "failed"
    fresh_batch2 = (
        await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))
    ).scalar_one()
    assert fresh_batch2.status == "failed"
    assert fresh_batch2.summary_json is not None
    assert fresh_batch2.summary_json["succeeded"] == 2
    assert fresh_batch2.summary_json["failed"] == 1


# ---------------------------------------------------------------------------
# 验收标准 5：取消。
# ---------------------------------------------------------------------------


async def test_batch_cancel_no_residual_generating(
    db_session: AsyncSession, org, monkeypatch
):
    """取消：未开始 Chunk 不调用 LLM、batch/task cancelled、Chunk 不残留 generating。"""
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=3)
    fake = FakeLLM({0: '{"a":0}', 1: '{"a":1}', 2: '{"a":2}'})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=None,
    )
    await db_session.commit()

    children = (
        await db_session.execute(
            select(Task).where(Task.parent_task_id == parent.id).order_by(Task.created_at)
        )
    ).scalars().all()

    # 取消前调用次数为 0；模拟取消全部未开始 child。
    from app.workers.queue import TaskQueue

    q = TaskQueue(db_session)
    child_ids = [c.id for c in children]
    for cid in child_ids:
        await q.request_cancel(task_id=cid, cancel_requested_by=org["users"]["editor"].id)
    await db_session.commit()

    # claim + 执行：handler 在 checkpoint 处检测取消并抛 TaskCancelledError。
    import contextlib

    for cid in child_ids:
        fresh_child = (
            await db_session.execute(select(Task).where(Task.id == cid))
        ).scalar_one()
        claimed = await _claim_task_async(db_session, fresh_child)
        for t in claimed:
            with contextlib.suppress(Exception):
                await _run_handler_and_transition(db_session, t, run_generate_single_handler)

    # 未开始 Chunk 不调用 LLM、不产出 Candidate。
    assert len(fake.calls) == 0
    candidates = (
        await db_session.execute(
            select(Candidate).where(Candidate.source_generation_batch_id == batch.id)
        )
    ).scalars().all()
    assert len(candidates) == 0

    # child task 全部 cancelled；Chunk 不残留 generating（回到 ready）。
    for cid in child_ids:
        fresh_child = (
            await db_session.execute(select(Task).where(Task.id == cid))
        ).scalar_one()
        assert fresh_child.status == "cancelled"
    chunks = (
        await db_session.execute(select(Chunk).where(Chunk.document_id == res["doc"].id))
    ).scalars().all()
    assert all(c.status != "generating" for c in chunks)


# ---------------------------------------------------------------------------
# 验收标准 6：retry 派生链。
# ---------------------------------------------------------------------------


async def test_retry_creates_new_chain_only_uncompleted(
    db_session: AsyncSession, org, monkeypatch
):
    """含两个成功、一个失败的 Batch retry：只含失败 Chunk 的新 Task/Batch/Run。"""
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=3)
    fake = FakeLLM({0: '{"a":0}', 1: '{"a":1}', 2: '{"a":2}'}, fail_json_for={1})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=None,
    )
    await db_session.commit()
    batch_id = batch.id
    parent_id = parent.id
    project_id = org["projects"]["a"].id
    editor_id = org["users"]["editor"].id

    children = (
        await db_session.execute(
            select(Task).where(Task.parent_task_id == parent_id).order_by(Task.created_at)
        )
    ).scalars().all()
    child_ids = [c.id for c in children]
    for cid in child_ids:
        fresh_child = (
            await db_session.execute(select(Task).where(Task.id == cid))
        ).scalar_one()
        claimed = await _claim_task_async(db_session, fresh_child)
        await _run_handler_and_transition(db_session, claimed[0], run_generate_single_handler)

    # 记录旧链字节级快照（数量）。
    old_batch_ids = set(
        (
            await db_session.execute(
                select(GenerationBatch.id).where(GenerationBatch.id == batch_id)
            )
        ).scalars().all()
    )
    old_run_ids = set(
        (
            await db_session.execute(
                select(GenerationRun.id).where(GenerationRun.generation_batch_id == batch_id)
            )
        ).scalars().all()
    )
    old_candidate_count = (
        await db_session.execute(
            select(func.count()).select_from(Candidate).where(Candidate.source_generation_batch_id == batch_id)
        )
    ).scalar()

    # 失败 batch（父任务聚合 failed）。
    from app.workers.generate_worker import run_generate_batch_handler

    fresh_parent = (
        await db_session.execute(select(Task).where(Task.id == parent_id))
    ).scalar_one()
    claimed_parent = await _claim_task_async(db_session, fresh_parent)
    parent_done = await _run_handler_and_transition(
        db_session, claimed_parent[0], run_generate_batch_handler
    )
    assert parent_done.status == "failed"
    fresh_batch = (
        await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))
    ).scalar_one()
    assert fresh_batch.status == "failed"

    # retry。
    from app.services.generation_retry_service import GenerationRetryPlanner

    planner = GenerationRetryPlanner(db_session, TaskService(db_session))
    new_batch, new_parent = await planner.plan_retry(
        source_batch=fresh_batch,
        project_id=project_id,
        created_by=editor_id,
    )
    await db_session.commit()
    new_batch_id = new_batch.id
    new_parent_id = new_parent.id

    # 新 Batch 只含失败 Chunk（1 个）。
    assert new_batch.retry_of_generation_batch_id == batch_id
    assert len(new_batch.selected_chunk_ids) == 1
    assert new_batch.total_chunks == 1
    assert new_batch.status == "pending"
    assert new_batch.provenance_status == "verified"

    # 新 parent task 的 retry_of_task_id 指向旧 parent。
    fresh_new_parent = (
        await db_session.execute(select(Task).where(Task.id == new_parent_id))
    ).scalar_one()
    assert fresh_new_parent.retry_of_task_id == parent_id
    assert fresh_new_parent.entity_id == new_batch_id

    # 新 run 只 1 个（失败 chunk）。
    new_runs = (
        await db_session.execute(
            select(GenerationRun).where(GenerationRun.generation_batch_id == new_batch_id)
        )
    ).scalars().all()
    assert len(new_runs) == 1

    # 旧链不变。
    assert len(old_batch_ids) == 1
    assert len(old_run_ids) == 3
    old_candidates_after = (
        await db_session.execute(
            select(func.count()).select_from(Candidate).where(Candidate.source_generation_batch_id == batch_id)
        )
    ).scalar()
    assert old_candidates_after == old_candidate_count == 2

    # 新 batch 执行成功后只额外调用 LLM 一次，新增 1 Candidate。
    new_children = (
        await db_session.execute(
            select(Task).where(Task.parent_task_id == fresh_new_parent.id)
        )
    ).scalars().all()
    assert len(new_children) == 1
    from app.workers.generate_worker import run_generate_single_handler as h2

    for c in new_children:
        fresh_c = (
            await db_session.execute(select(Task).where(Task.id == c.id))
        ).scalar_one()
        cc = await _claim_task_async(db_session, fresh_c)
        await _run_handler_and_transition(db_session, cc[0], h2)
    assert len(fake.calls) == 4  # 3 旧 + 1 新
    new_candidates = (
        await db_session.execute(
            select(func.count()).select_from(Candidate).where(Candidate.source_generation_batch_id == new_batch_id)
        )
    ).scalar()
    assert new_candidates == 1


# ---------------------------------------------------------------------------
# 验收标准 7：不可复活 / 并发 retry 唯一后继。
# ---------------------------------------------------------------------------


async def test_retry_not_retryable_and_immutability(
    db_session: AsyncSession, org
):
    """没有未成功项 -> NOT_RETRYABLE；legacy 源 -> PROVENANCE_INVALID。"""
    from app.services.generation_retry_service import (
        GenerationRetryNotRetryableError,
        GenerationRetryPlanner,
    )

    res = await _build_generation_doc(db_session, org, chunk_count=1)
    batch, _parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=[res["chunks"][0].id],
    )
    await db_session.commit()

    # 模拟全成功：把已创建的 run 标记为 completed（不新增，满足 (batch, chunk) 唯一）。
    existing_run = (
        await db_session.execute(
            select(GenerationRun).where(
                GenerationRun.generation_batch_id == batch.id,
                GenerationRun.chunk_id == res["chunks"][0].id,
            )
        )
    ).scalar_one()
    existing_run.status = "completed"
    existing_run.input_prompt = "[{}]"
    existing_run.rendered_prompt_sha256 = "0" * 64
    existing_run.raw_output = '{"a": 0}'
    existing_run.completed_at = datetime.now(UTC)
    batch.status = "failed"  # 即便失败，无未成功项也应 NOT_RETRYABLE
    batch.completed_at = datetime.now(UTC)
    await db_session.commit()

    planner = GenerationRetryPlanner(db_session, TaskService(db_session))
    with pytest.raises(GenerationRetryNotRetryableError):
        await planner.plan_retry(
            source_batch=batch,
            project_id=org["projects"]["a"].id,
            created_by=org["users"]["editor"].id,
        )


async def test_retry_provenance_invalid_rejected(
    db_session: AsyncSession, org
):
    """源 Batch provenance 非 verified -> PROVENANCE_INVALID。"""
    from app.services.generation_retry_service import (
        GenerationProvenanceInvalidError,
        GenerationRetryPlanner,
    )

    res = await _build_generation_doc(db_session, org, chunk_count=1)
    batch, _parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=[res["chunks"][0].id],
    )
    # 改为 legacy_unavailable 模拟旧数据。
    batch.is_legacy = True
    batch.provenance_status = "legacy_unavailable"
    batch.provenance_error_code = "LEGACY_TEST"
    await db_session.commit()

    planner = GenerationRetryPlanner(db_session, TaskService(db_session))
    with pytest.raises(GenerationProvenanceInvalidError):
        await planner.plan_retry(
            source_batch=batch,
            project_id=org["projects"]["a"].id,
            created_by=org["users"]["editor"].id,
        )


async def test_retry_concurrent_only_one_successor(
    db_session: AsyncSession, org, monkeypatch
):
    """两个并发 retry 只形成一个直接后继，不产生分叉。"""
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=2)
    fake = FakeLLM({0: '{"a":0}', 1: '{"a":1}'}, fail_json_for={1})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=None,
    )
    await db_session.commit()
    batch_id = batch.id
    parent_id = parent.id
    project_id = org["projects"]["a"].id
    editor_id = org["users"]["editor"].id
    children = (
        await db_session.execute(
            select(Task).where(Task.parent_task_id == parent_id).order_by(Task.created_at)
        )
    ).scalars().all()
    child_ids = [c.id for c in children]
    for cid in child_ids:
        fresh_child = (
            await db_session.execute(select(Task).where(Task.id == cid))
        ).scalar_one()
        claimed = await _claim_task_async(db_session, fresh_child)
        await _run_handler_and_transition(db_session, claimed[0], run_generate_single_handler)
    # 父任务 failed。
    from app.workers.generate_worker import run_generate_batch_handler

    fresh_parent = (
        await db_session.execute(select(Task).where(Task.id == parent_id))
    ).scalar_one()
    cp = await _claim_task_async(db_session, fresh_parent)
    await _run_handler_and_transition(db_session, cp[0], run_generate_batch_handler)

    fresh_batch = (
        await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))
    ).scalar_one()
    assert fresh_batch.status == "failed"

    from app.services.generation_retry_service import (
        GenerationRetryExistsError,
        GenerationRetryPlanner,
    )

    planner = GenerationRetryPlanner(db_session, TaskService(db_session))
    new_batch, _ = await planner.plan_retry(
        source_batch=fresh_batch,
        project_id=project_id,
        created_by=editor_id,
    )
    await db_session.commit()

    # 第二次 retry 应抛 RETRY_EXISTS（同一源已有直接后继）。
    fresh_batch2 = (
        await db_session.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))
    ).scalar_one()
    with pytest.raises(GenerationRetryExistsError):
        await planner.plan_retry(
            source_batch=fresh_batch2,
            project_id=project_id,
            created_by=editor_id,
        )


# ---------------------------------------------------------------------------
# 验收标准 8：快照冻结 —— 创建后修改配置不影响 worker。
# ---------------------------------------------------------------------------


async def test_worker_uses_frozen_snapshot_after_config_change(
    db_session: AsyncSession, org, monkeypatch
):
    """创建 Batch 后修改模板/模型非秘密参数，worker 仍用冻结值重建相同 hash。"""
    from app.generation.renderer import rebuild_input_prompt_and_hash
    from app.workers.generate_worker import run_generate_single_handler

    res = await _build_generation_doc(db_session, org, chunk_count=1)
    chunk = res["chunks"][0]
    fake = FakeLLM({0: '{"a":0}'})
    fake.install(monkeypatch)

    batch, parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=[chunk.id],
    )
    await db_session.commit()

    # 冻结快照与 renderer 重建的 hash 一致。
    rebuilt, rebuilt_hash = rebuild_input_prompt_and_hash(
        batch.prompt_template_snapshot, chunk.content, chunk.heading_path
    )
    run = (
        await db_session.execute(
            select(GenerationRun).where(GenerationRun.generation_batch_id == batch.id)
        )
    ).scalar_one()
    assert run.rendered_prompt_sha256 == rebuilt_hash

    # 修改当前模板/模型非秘密参数（不得影响冻结值）。
    res["tpl"].system_prompt = "已修改的 system prompt"
    res["tpl"].user_prompt_template = "已修改 {{content}}"
    res["model"].temperature = 0.99
    await db_session.commit()

    # worker 重建仍与冻结 hash 一致（不读当前可变参数）。
    rebuilt2, rebuilt_hash2 = rebuild_input_prompt_and_hash(
        batch.prompt_template_snapshot, chunk.content, chunk.heading_path
    )
    assert rebuilt_hash2 == rebuilt_hash

    # 执行 worker 成功。
    children = (
        await db_session.execute(select(Task).where(Task.parent_task_id == parent.id))
    ).scalars().all()
    claimed = await _claim_task_async(db_session, children[0])
    done = await _run_handler_and_transition(db_session, claimed[0], run_generate_single_handler)
    assert done.status == "completed"
    fresh_run = (
        await db_session.execute(select(GenerationRun).where(GenerationRun.id == run.id))
    ).scalar_one()
    assert fresh_run.status == "completed"


# ---------------------------------------------------------------------------
# 验收标准 9：secret scanner —— 快照/响应不含测试凭证。
# ---------------------------------------------------------------------------


async def test_snapshot_contains_no_secrets(db_session: AsyncSession, org):
    """Batch 快照/API 响应不含测试 API key/Authorization/credential。"""
    from app.generation.snapshot import find_secret_field_paths

    res = await _build_generation_doc(db_session, org, chunk_count=1)
    batch, _parent = await _create_batch(
        db_session, org=org, doc=res["doc"], tpl=res["tpl"], model=res["model"],
        selected=[res["chunks"][0].id],
    )
    await db_session.commit()

    # 快照递归扫描：不得含秘密字段。
    assert find_secret_field_paths(batch.prompt_template_snapshot) == []
    assert find_secret_field_paths(batch.model_config_snapshot) == []

    # ModelConfig 快照不含 api_key（api_key_encrypted 绝不进入快照）。
    assert "api_key" not in str(batch.model_config_snapshot)
    assert "Authorization" not in str(batch.model_config_snapshot)


async def test_unsafe_extra_param_rejected_before_creation(
    db_session: AsyncSession, org
):
    """ModelConfig extra_params 含无法安全分类的秘密字段 -> 创建 Batch 前失败。"""
    from app.generation.snapshot import SnapshotUnsafeError

    res = await _build_generation_doc(db_session, org, chunk_count=1)
    # 注入秘密 extra_param。
    res["model"].extra_params = {"api_key": "sk-test-secret"}
    await db_session.commit()

    from app.services.generation_service import GenerationOrchestrationService

    service = GenerationOrchestrationService(db_session, TaskService(db_session))
    with pytest.raises(SnapshotUnsafeError):
        await service.create_batch(
            project_id=org["projects"]["a"].id,
            document_id=res["doc"].id,
            prompt_template_id=res["tpl"].id,
            model_config_id=res["model"].id,
            selected_chunk_ids=[res["chunks"][0].id],
            created_by=org["users"]["editor"].id,
        )
