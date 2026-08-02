"""generate worker（T08 §5.4）：只读冻结快照、核验 rendered prompt hash、明确结果。

child handler ``generate_single``：从 GenerationBatch 冻结快照 + Chunk 重建
input_prompt，核对 snapshot/hash/renderer，调用 LLM（凭证在调用前即时解析，
不进入快照），持久化 raw output/Candidate/usage -> 标记 run/child task completed
-> 更新批次计数。LLM 返回非 JSON 时该 run failed，不得用 ``{"raw_text": ...}``
创建看似合格的 Candidate。

parent handler ``generate_batch``：聚合全部 child task 的真实终态；任一 child
最终失败 -> batch/parent failed；全部成功 -> completed；未全部终态 -> 回队退避
轮询。绝不吞异常后假 completed。

失败/取消收敛：handler 注册终态钩子，runner 在回滚业务写入后以全新会话把
GenerationRun / GenerationBatch / Chunk 收敛到 failed/cancelled（CAS，不覆盖
已完成产物）。取消时未开始 Chunk 不调用 LLM、不产出 Candidate。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation.renderer import (
    RENDERER_VERSION,
    rebuild_input_prompt_and_hash,
)
from app.models.chunk import Chunk
from app.models.config import ModelConfig
from app.models.document import Document
from app.models.generation import Candidate, GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.task import LlmUsageLog, Task
from app.workers.execution import ExecutionContext
from app.workers.queue import TERMINAL_STATUSES
from llm import LLMClient


class GenerationRunError(Exception):
    """生成 run 的明确业务失败（脱敏错误信息进入 run.error_message）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def run_generate_single_handler(ctx: ExecutionContext) -> None:
    """generate_single:v1 handler（child）。worker 使用独立 session 事务。"""
    payload = ctx.payload
    chunk_id = uuid.UUID(str(payload["chunk_id"]))
    batch_id = uuid.UUID(str(payload["generation_batch_id"]))
    run_id = uuid.UUID(str(payload["generation_run_id"]))
    db = ctx.db

    # 项目链复核（以 task.project_id 为锚点）。
    from app.authz import verify_project_chain

    await verify_project_chain(
        db, ctx.project_id,
        [(Chunk, chunk_id), (GenerationBatch, batch_id), (GenerationRun, run_id)],
        detail="生成任务项目链不一致",
    )

    # 注册终态钩子：child task 失败/取消时收敛该 run + chunk（CAS）。
    ctx.set_terminal_hook(_make_run_terminal_hook(run_id, chunk_id))

    await ctx.checkpoint()

    batch, prompt_snapshot, model_snapshot = await _load_batch_and_verify(ctx, batch_id)
    chunk = (await db.execute(select(Chunk).where(Chunk.id == chunk_id))).scalar_one_or_none()
    if chunk is None:
        raise GenerationRunError("CHUNK_NOT_FOUND", "Chunk 不存在")
    run = (await db.execute(select(GenerationRun).where(GenerationRun.id == run_id))).scalar_one_or_none()
    if run is None or run.generation_batch_id != batch.id:
        raise GenerationRunError("RUN_NOT_FOUND", "GenerationRun 不存在或不属于该批次")
    if run.provenance_status != "verified":
        raise GenerationRunError("RUN_NOT_VERIFIED", "GenerationRun provenance 非 verified")

    # 幂等保护：completed run 不可重放。
    if run.status == "completed":
        return

    # 从冻结快照 + Chunk 重建 input_prompt，核对 hash（任务卡 §5.4）。
    rebuilt, rebuilt_hash = rebuild_input_prompt_and_hash(
        prompt_snapshot, chunk.content, chunk.heading_path
    )
    if run.rendered_prompt_sha256 is None or run.rendered_prompt_sha256 != rebuilt_hash:
        raise GenerationRunError(
            "RENDERED_PROMPT_HASH_MISMATCH", "渲染 prompt hash 与批次快照不一致"
        )

    # 标记 run processing + chunk generating。
    run.status = "processing"
    chunk.status = "generating"
    await db.flush()
    await ctx.checkpoint()

    # 凭证调用前即时解析；其余参数只读快照。
    mc = await _resolve_model_credential(db, batch.model_config_id)
    client = LLMClient(
        base_url=str(model_snapshot["base_url"]),
        api_key=mc.api_key_encrypted,
        model_name=str(model_snapshot["model_name"]),
        temperature=model_snapshot.get("temperature"),
        max_tokens=model_snapshot.get("max_tokens"),
    )
    messages = json.loads(rebuilt)
    try:
        response = await client.chat_completion(messages, response_format={"type": "json_object"})
    except Exception as exc:  # noqa: BLE001 - 转为明确 run 失败
        raise GenerationRunError(
            "LLM_CALL_FAILED", f"LLM 调用失败: {type(exc).__name__}"
        ) from exc
    await ctx.checkpoint()

    # 解析输出：非 JSON -> run failed（不得创建看似合格的 Candidate）。
    try:
        content = json.loads(response.content)
    except json.JSONDecodeError as exc:
        raise GenerationRunError("LLM_INVALID_JSON", "LLM 返回非 JSON，run 失败") from exc

    # 成功：持久化 raw output/Candidate/usage -> 标记 run/child task completed -> 更新批次计数。
    run.raw_output = response.content
    run.status = "completed"
    run.completed_at = datetime.now(UTC)

    candidate = Candidate(
        generation_run_id=run.id,
        chunk_id=chunk.id,
        content=content,
        candidate_type=str(prompt_snapshot["task_type"]),
        status="ai_generated",
        source_generation_batch_id=batch.id,
    )
    db.add(candidate)

    usage_log = LlmUsageLog(
        project_id=ctx.project_id,
        task_id=ctx.task_id,
        model_config_id=batch.model_config_id,
        prompt_template_id=batch.prompt_template_id,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        latency_ms=response.latency_ms,
        status="success",
    )
    db.add(usage_log)

    chunk.status = "generated"
    await db.flush()

    # 更新批次计数（completed_chunks 只计 Candidate 已 flush 成功的 run）。
    await _increment_batch_completed(db, batch.id)
    await db.flush()


async def _load_batch_and_verify(
    ctx: ExecutionContext, batch_id: uuid.UUID
) -> tuple[GenerationBatch, dict, dict]:
    """加载冻结 Batch 并核验快照完整性。

    返回 (batch, prompt_snapshot, model_snapshot)。不读取模板/模型当前可变参数。
    """
    batch = (
        await ctx.db.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))
    ).scalar_one_or_none()
    if batch is None:
        raise GenerationRunError("BATCH_NOT_FOUND", "生成批次不存在")
    if batch.provenance_status != "verified":
        raise GenerationRunError("BATCH_NOT_VERIFIED", "生成批次 provenance 非 verified")
    if batch.renderer_version != RENDERER_VERSION:
        raise GenerationRunError(
            "RENDERER_MISMATCH", f"renderer 版本不匹配: {batch.renderer_version} != {RENDERER_VERSION}"
        )
    prompt_snapshot = batch.prompt_template_snapshot
    model_snapshot = batch.model_config_snapshot
    if prompt_snapshot is None or model_snapshot is None:
        raise GenerationRunError("BATCH_NO_SNAPSHOT", "生成批次缺少冻结快照")
    return batch, prompt_snapshot, model_snapshot


async def _resolve_model_credential(db: AsyncSession, model_config_id: uuid.UUID) -> ModelConfig:
    """调用前即时解析凭证（仅读取 api_key，不进入快照）。"""
    mc = (await db.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))).scalar_one_or_none()
    if mc is None:
        raise GenerationRunError("MODEL_CONFIG_NOT_FOUND", "模型配置不存在")
    return mc


async def _increment_batch_completed(db: AsyncSession, batch_id: uuid.UUID) -> None:
    """CAS 递增 completed_chunks（不覆盖终态/超过 total）。"""
    await db.execute(
        update(GenerationBatch)
        .where(
            GenerationBatch.id == batch_id,
            GenerationBatch.status.in_(("pending", "processing")),
            GenerationBatch.completed_chunks < GenerationBatch.total_chunks,
        )
        .values(completed_chunks=GenerationBatch.completed_chunks + 1)
    )


async def run_generate_batch_handler(ctx: ExecutionContext) -> None:
    """generate_batch:v1 handler（parent）：聚合 child task 真实终态。"""
    payload = ctx.payload
    document_id = uuid.UUID(str(payload["document_id"]))
    batch_id = uuid.UUID(str(payload["generation_batch_id"]))
    db = ctx.db

    from app.authz import verify_project_chain

    await verify_project_chain(
        db, ctx.project_id, [(Document, document_id), (GenerationBatch, batch_id)],
        detail="批量生成任务项目链不一致",
    )

    # 注册终态钩子：失败/取消时收敛 Batch 与未完成 Chunk 状态。
    ctx.set_terminal_hook(_make_batch_terminal_hook(batch_id, document_id))

    await ctx.checkpoint()

    batch = (await db.execute(select(GenerationBatch).where(GenerationBatch.id == batch_id))).scalar_one_or_none()
    if batch is None:
        raise GenerationRunError("BATCH_NOT_FOUND", "生成批次不存在")
    if batch.document_id != document_id:
        raise GenerationRunError("BATCH_DOC_MISMATCH", "生成批次与文档不匹配")

    if batch.status in ("completed", "failed", "cancelled"):
        # 幂等：终态批次不重放。
        return
    if batch.status == "pending":
        batch.status = "processing"
    await db.flush()

    # 聚合 child task 状态（T07 真实终态）。
    status_result = await db.execute(
        select(Task.status, func.count())
        .where(Task.parent_task_id == ctx.task_id)
        .group_by(Task.status)
    )
    status_counts = dict(status_result.all())
    total_children = sum(status_counts.values())
    if total_children == 0:
        # 本轮刚创建（子任务在同一事务提交）——下一轮聚合。
        ctx.requeue_after = 5
        await db.flush()
        return

    terminal_children = sum(
        count for status, count in status_counts.items() if status in TERMINAL_STATUSES
    )
    failed_children = sum(
        count for status, count in status_counts.items() if status in ("failed", "cancelled")
    )
    if failed_children:
        # 任一 child 最终失败 -> batch/parent failed（父任务绝不假 completed）。
        await _finalize_batch_failed(db, batch, status_counts)
        raise GenerationRunError("CHILD_FAILED", f"存在失败/取消子任务（{failed_children} 个）")
    if terminal_children < total_children:
        # 仍有运行中的子任务：回队退避轮询。
        ctx.requeue_after = 10
        await db.flush()
        return

    # 全部 completed：batch/parent completed。
    await _finalize_batch_completed(db, batch)
    await db.flush()


async def _finalize_batch_completed(db: AsyncSession, batch: GenerationBatch) -> None:
    """全部 child completed：批次 completed + summary + 文档状态恢复。"""
    batch.status = "completed"
    batch.completed_at = datetime.now(UTC)
    candidate_ids = [
        str(cid) for cid in (
            await db.execute(
                select(Candidate.id).where(Candidate.source_generation_batch_id == batch.id)
            )
        ).scalars().all()
    ]
    batch.summary_json = {
        "succeeded": batch.completed_chunks,
        "failed": 0,
        "cancelled": 0,
        "candidate_ids": candidate_ids,
        "failures": [],
    }
    doc = (await db.execute(select(Document).where(Document.id == batch.document_id))).scalar_one()
    doc.status = "generated"


async def _finalize_batch_failed(
    db: AsyncSession, batch: GenerationBatch, status_counts: dict[str, int]
) -> None:
    """任一 child 最终失败：batch/parent failed（不假 completed）。"""
    batch.status = "failed"
    batch.completed_at = datetime.now(UTC)
    failed_runs = (
        await db.execute(
            select(GenerationRun).where(
                GenerationRun.generation_batch_id == batch.id,
                GenerationRun.status == "failed",
            )
        )
    ).scalars().all()
    failures = [
        {
            "chunk_id": str(r.chunk_id),
            "code": "RUN_FAILED",
            "message": (r.error_message or "run 失败")[:500],
        }
        for r in failed_runs
    ]
    batch.summary_json = {
        "succeeded": batch.completed_chunks,
        "failed": len(failed_runs),
        "cancelled": int(status_counts.get("cancelled", 0)),
        "candidate_ids": [
            str(cid) for cid in (
                await db.execute(
                    select(Candidate.id).where(Candidate.source_generation_batch_id == batch.id)
                )
            ).scalars().all()
        ],
        "failures": failures,
    }


def _make_run_terminal_hook(run_id: uuid.UUID, chunk_id: uuid.UUID):
    """构造 child run 终态钩子（runner 失败/取消回滚后以新会话调用）。

    把 GenerationRun 收敛为 failed/cancelled（CAS），并把 Chunk 从 generating
    恢复为 ready，绝不覆盖已完成的 run/Candidate。
    """

    async def hook(db: AsyncSession, status: str, error_message: str | None) -> None:
        terminal = "failed" if status == "failed" else "cancelled"
        await db.execute(
            update(GenerationRun)
            .where(
                GenerationRun.id == run_id,
                GenerationRun.status.in_(("queued", "processing")),
            )
            .values(
                status=terminal,
                completed_at=datetime.now(UTC),
                error_message=(error_message or f"run {terminal}")[:500],
            )
        )
        await db.execute(
            update(Chunk)
            .where(Chunk.id == chunk_id, Chunk.status == "generating")
            .values(status="ready")
        )

    return hook


def _make_batch_terminal_hook(batch_id: uuid.UUID, document_id: uuid.UUID):
    """构造 Batch 终态钩子（runner 失败/取消回滚后以新会话调用）。

    用 CAS 收敛 Batch 与未完成 Chunk 状态，绝不覆盖已完成产物或已提交 Candidate。
    summary 从真实 run 状态计算（成功/失败/取消计数与失败明细）。
    """

    async def hook(db: AsyncSession, status: str, error_message: str | None) -> None:
        terminal = "failed" if status == "failed" else "cancelled"
        runs = (
            await db.execute(
                select(GenerationRun).where(GenerationRun.generation_batch_id == batch_id)
            )
        ).scalars().all()
        succeeded = sum(1 for r in runs if r.status == "completed")
        failed = sum(1 for r in runs if r.status == "failed")
        cancelled = sum(1 for r in runs if r.status == "cancelled")
        failures = [
            {"chunk_id": str(r.chunk_id), "code": "RUN_FAILED", "message": (r.error_message or "run 失败")[:500]}
            for r in runs if r.status == "failed"
        ]
        candidate_ids = [
            str(cid) for cid in (
                await db.execute(
                    select(Candidate.id).where(Candidate.source_generation_batch_id == batch_id)
                )
            ).scalars().all()
        ]
        await db.execute(
            update(GenerationBatch)
            .where(
                GenerationBatch.id == batch_id,
                GenerationBatch.status.in_(("pending", "processing")),
            )
            .values(
                status=terminal,
                completed_at=datetime.now(UTC),
                completed_chunks=succeeded,
                summary_json={
                    "succeeded": succeeded,
                    "failed": failed,
                    "cancelled": cancelled,
                    "candidate_ids": candidate_ids,
                    "failures": failures,
                },
            )
        )
        # 未开始 Chunk 状态恢复（不残留 generating）。
        await db.execute(
            update(Chunk)
            .where(Chunk.document_id == document_id, Chunk.status == "generating")
            .values(status="ready")
        )
        doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
        if doc.status == "generating":
            doc.status = "chunked"

    return hook
