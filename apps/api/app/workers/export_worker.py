"""T11 Export handlers: 一致快照封存 -> 从快照渲染 -> versioned upload -> seal+finalize。

任务卡 §5 流程：
1. worker 在 REPEATABLE READ 事务中只消费 T10 finalized composition snapshot、
   T09 approved CuratedRevision/Evidence snapshot、T08 GenerationBatch/Prompt/Model
   snapshot、T06 ChunkSet snapshot、T03 ParserProfile snapshot；构建完整 manifest
   并一次 INSERT 封存。
2. 事务提交后，formatter 只从 SnapshotManifest 渲染 payload；绝不重新读取当前
   CuratedItem/Evidence/ParserProfile/PromptTemplate/ModelConfig。
3. 计算 payload hash 后使用 key：``{project_id}/exports/{export_id}/payload-{sha256}.{ext}``
   （manifest 用同目录 ``manifest-{sha256}.json``）。
4. outputs bucket 必须启用 versioning；保存每次 PUT 的 version_id；同 key 已存在时
   仅在 bytes/hash 完全一致时复用。
5. 上传 payload 与 manifest 后，同一数据库事务：INSERT 经 trigger 验证的 artifact
   seal，再以 run token CAS 更新 Export 的 seal/对象字段与 completed。deferred
   constraint 在 commit 前复核；任一步失败整笔回滚。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.config import ExportProfile
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem
from app.models.export import Export
from app.services.export_manifest import (
    ProvenanceSnapshotMissingError,
    build_export_manifest,
)
from app.services.export_service import (
    EXPORT_PROCESSING,
    ExportService,
)
from app.workers.errors import TaskError, TaskErrorCode
from app.workers.execution import ExecutionContext
from storage import get_storage_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Formatter（只读冻结 manifest 渲染 payload）
# ---------------------------------------------------------------------------


def _member_content(member: dict) -> dict:
    """从 manifest 冻结成员取 CuratedRevision.content（绝不读当前业务表）。"""
    return (member.get("curated_revision") or {}).get("content") or {}


def _member_evidence_text(member: dict) -> str:
    """从 manifest 冻结证据链接拼接 evidence_text（引用 quote）。"""
    parts = []
    for ev in member.get("evidence") or []:
        quote = ev.get("quote_text")
        if quote:
            parts.append(quote)
    return "; ".join(parts)


def _format_sft_jsonl(members: list[dict]) -> str:
    """One JSON object per line: {"instruction": ..., "input": ..., "output": ...}"""
    lines = []
    for member in members:
        content = _member_content(member)
        record = {
            "instruction": content.get("instruction", content.get("question", "")),
            "input": content.get("input", ""),
            "output": content.get("output", content.get("answer", "")),
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines)


def _format_qa_json(members: list[dict]) -> str:
    """JSON array: [{"question": ..., "answer": ...}]"""
    records = []
    for member in members:
        content = _member_content(member)
        records.append({
            "question": content.get("question", content.get("instruction", "")),
            "answer": content.get("answer", content.get("output", "")),
        })
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_messages(members: list[dict]) -> str:
    """JSON array: [{"messages": [{"role":"system",...},{"role":"user",...},{"role":"assistant",...}]}]"""
    records = []
    for member in members:
        content = _member_content(member)
        messages = [
            {"role": "system", "content": content.get("system", "你是一个压气机设计领域的专家。")},
            {"role": "user", "content": content.get("instruction", content.get("question", ""))},
            {"role": "assistant", "content": content.get("output", content.get("answer", ""))},
        ]
        records.append({"messages": messages})
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_alpaca(members: list[dict]) -> str:
    """JSON array: [{"instruction": ..., "input": ..., "output": ...}]"""
    records = []
    for member in members:
        content = _member_content(member)
        records.append({
            "instruction": content.get("instruction", content.get("question", "")),
            "input": content.get("input", ""),
            "output": content.get("output", content.get("answer", "")),
        })
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_sharegpt(members: list[dict]) -> str:
    """JSON array: [{"conversations": [{"from":"human",...},{"from":"gpt",...}]}]"""
    records = []
    for member in members:
        content = _member_content(member)
        conversations = [
            {"from": "human", "value": content.get("instruction", content.get("question", ""))},
            {"from": "gpt", "value": content.get("output", content.get("answer", ""))},
        ]
        records.append({"conversations": conversations})
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_benchmark_json(members: list[dict]) -> str:
    """JSON array: [{"question": ..., "reference_answer": ..., "evidence": ...}]"""
    records = []
    for member in members:
        content = _member_content(member)
        records.append({
            "question": content.get("question", content.get("instruction", "")),
            "reference_answer": content.get("reference_answer", content.get("answer", content.get("output", ""))),
            "evidence": content.get("evidence", _member_evidence_text(member)),
        })
    return json.dumps(records, ensure_ascii=False, indent=2)


FORMAT_HANDLERS = {
    "sft_jsonl": _format_sft_jsonl,
    "qa_json": _format_qa_json,
    "messages": _format_messages,
    "alpaca": _format_alpaca,
    "sharegpt": _format_sharegpt,
    "benchmark_json": _format_benchmark_json,
}

FORMAT_CONTENT_TYPES = {
    "sft_jsonl": "application/jsonl",
    "qa_json": "application/json",
    "messages": "application/json",
    "alpaca": "application/json",
    "sharegpt": "application/json",
    "benchmark_json": "application/json",
}

FORMAT_EXTENSIONS = {
    "sft_jsonl": "jsonl",
    "qa_json": "json",
    "messages": "json",
    "alpaca": "json",
    "sharegpt": "json",
    "benchmark_json": "json",
}


def render_payload_from_manifest(manifest: dict, fmt: str) -> str:
    """只读 SnapshotManifest 渲染 payload（任务卡 §5.2）。"""
    handler = FORMAT_HANDLERS.get(fmt)
    if handler is None:
        raise ValueError(f"不支持的导出格式: {fmt}")
    members = manifest.get("members") or []
    if not members:
        raise ValueError("没有可导出的条目")
    return handler(members)


def render_payload_sha256(payload: str) -> str:
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 共享导出逻辑
# ---------------------------------------------------------------------------


async def _load_memberships(db: AsyncSession, source_type: str, source_id: uuid.UUID) -> list:
    """加载 T10 finalized membership（含固定 revision/approval hash）。"""
    if source_type == "dataset":
        result = await db.execute(
            select(DatasetItem).where(DatasetItem.dataset_id == source_id).order_by(DatasetItem.ordinal)
        )
        return list(result.scalars().all())
    result = await db.execute(
        select(BenchmarkCase).where(BenchmarkCase.benchmark_id == source_id).order_by(BenchmarkCase.ordinal)
    )
    return list(result.scalars().all())


async def _mark_export_processing(db: AsyncSession, export_id: uuid.UUID, task_id: uuid.UUID) -> None:
    """Export queued -> processing（T07 fenced attempt 推进）。"""
    await db.execute(
        update(Export)
        .where(
            Export.id == export_id,
            Export.is_legacy.is_(False),
            Export.status == "queued",
        )
        .values(status=EXPORT_PROCESSING, task_id=task_id, updated_at=datetime.now(UTC))
    )


async def _make_export_terminal_hook(export_id: uuid.UUID):
    """导出终态钩子：任务失败/取消时把 Export 收敛为 failed（不覆盖已完成产物）。"""

    async def _hook(db: AsyncSession, status: str, error_message: str | None) -> None:
        if status == "cancelled":
            await db.execute(
                update(Export)
                .where(
                    Export.id == export_id,
                    Export.is_legacy.is_(False),
                    Export.status.in_(("queued", "processing")),
                )
                .values(
                    status="failed",
                    error_code="TASK_CANCELLED",
                    error_message="导出任务已取消",
                    updated_at=datetime.now(UTC),
                )
            )
            return
        await db.execute(
            update(Export)
            .where(
                Export.id == export_id,
                Export.is_legacy.is_(False),
                Export.status.in_(("queued", "processing")),
            )
            .values(
                status="failed",
                error_code="BUSINESS_ERROR",
                error_message=error_message or "导出失败",
                updated_at=datetime.now(UTC),
            )
        )

    return _hook


async def _export_common(
    ctx: ExecutionContext,
    *,
    export_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    export_profile_id: uuid.UUID,
    created_by: uuid.UUID,
) -> None:
    """T11 导出公共流程（见模块 docstring）。"""
    db = ctx.db
    export_service = ExportService(db)
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )

    # 注册业务终态钩子：失败/取消时把 Export 收敛为 failed。
    ctx.set_terminal_hook(await _make_export_terminal_hook(export_id))

    # 1. 校验 Export 存在且 queued/processing，标记 processing。
    export = (
        await db.execute(select(Export).where(Export.id == export_id))
    ).scalar_one_or_none()
    if export is None:
        raise RuntimeError(f"导出记录不存在: {export_id}")
    if export.status not in ("queued", "processing"):
        raise RuntimeError(f"导出状态非法: {export.status}")
    await _mark_export_processing(db, export_id, ctx.task_id)

    # 2. 读取 T10 finalized container + membership。
    if source_type == "dataset":
        container = (await db.execute(select(Dataset).where(Dataset.id == source_id))).scalar_one_or_none()
    else:
        container = (await db.execute(select(Benchmark).where(Benchmark.id == source_id))).scalar_one_or_none()
    if container is None:
        raise RuntimeError(f"{source_type} 不存在: {source_id}")
    if container.status != "finalized":
        raise RuntimeError("source 未 finalize，禁止导出")
    memberships = await _load_memberships(db, source_type, source_id)
    if not memberships:
        raise RuntimeError("没有可导出的条目")
    await ctx.checkpoint()

    # 3. ExportProfile + 非敏感快照。
    profile = (
        await db.execute(select(ExportProfile).where(ExportProfile.id == export_profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise RuntimeError("导出配置不存在")
    profile_snapshot = export_service.build_profile_snapshot(profile)
    profile_hash = export_service.profile_snapshot_sha256(profile_snapshot)
    fmt = profile.format
    if fmt not in FORMAT_HANDLERS:
        raise RuntimeError(f"不支持的导出格式: {fmt}")
    await ctx.checkpoint()

    # 4. 构建完整 manifest（只消费冻结引用；缺失即抛 ProvenanceSnapshotMissingError）。
    try:
        manifest, manifest_hash = await build_export_manifest(
            db,
            export_id=export_id,
            project_id=ctx.project_id,
            requested_by=created_by,
            container=container,
            memberships=memberships,
            export_profile=profile,
            profile_snapshot=profile_snapshot,
            profile_snapshot_hash=profile_hash,
        )
    except ProvenanceSnapshotMissingError as exc:
        raise TaskError(TaskErrorCode.PROVENANCE_SNAPSHOT_MISSING, str(exc)) from exc
    await ctx.checkpoint()

    # 5. 插入 SnapshotManifest（不可变）。
    snapshot = await export_service.insert_snapshot_manifest(
        export_id=export_id,
        project_id=ctx.project_id,
        manifest=manifest,
        manifest_hash=manifest_hash,
    )
    await db.flush()

    # 6. 只读 manifest 渲染 payload。
    output_text = render_payload_from_manifest(manifest, fmt)
    payload_sha256 = render_payload_sha256(output_text)
    payload_bytes = output_text.encode("utf-8")
    ext = FORMAT_EXTENSIONS.get(fmt, "json")
    content_type = FORMAT_CONTENT_TYPES.get(fmt, "application/json")
    await ctx.checkpoint()

    # 7. versioned 上传：内容寻址 key（每次 Export 唯一，同扩展名不同格式不共享 key）。
    base_key = f"{ctx.project_id}/exports/{export_id}"
    output_key = f"{base_key}/payload-{payload_sha256}.{ext}"
    output_version_id = storage.put_object_versioned(
        settings.minio_bucket_outputs, output_key, payload_bytes, content_type, sha256=payload_sha256,
    )
    if not output_version_id:
        raise RuntimeError("output 上传未返回 version_id")
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest_key = f"{base_key}/manifest-{manifest_hash}.json"
    manifest_version_id = storage.put_object_versioned(
        settings.minio_bucket_outputs, manifest_key, manifest_bytes, "application/json", sha256=manifest_hash,
    )
    if not manifest_version_id:
        raise RuntimeError("manifest 上传未返回 version_id")
    await ctx.checkpoint()

    # 8. 同一事务：INSERT seal（trigger 复算） + fenced finalize。
    seal = await export_service.insert_artifact_seal(
        export_id=export_id,
        snapshot_manifest_id=snapshot.id,
        manifest_bucket=settings.minio_bucket_outputs,
        manifest_key=manifest_key,
        manifest_object_version_id=manifest_version_id,
        manifest_sha256=manifest_hash,
        manifest_size=len(manifest_bytes),
        manifest_content_type="application/json",
        output_bucket=settings.minio_bucket_outputs,
        output_key=output_key,
        output_object_version_id=output_version_id,
        output_sha256=payload_sha256,
        output_size=len(payload_bytes),
        output_content_type=content_type,
    )
    ok = await export_service.finalize_completed(
        export_id=export_id,
        run_token=ctx.run_token,
        expected_state_version=ctx.attempt_no,
        artifact_seal_id=seal.id,
        snapshot_manifest_id=snapshot.id,
        output_bucket=settings.minio_bucket_outputs,
        output_key=output_key,
        output_object_version_id=output_version_id,
        output_sha256=payload_sha256,
        output_size=len(payload_bytes),
        content_type=content_type,
        manifest_key=manifest_key,
        manifest_object_version_id=manifest_version_id,
        manifest_sha256=manifest_hash,
        format=fmt,
        item_count=len(memberships),
        task_id=ctx.task_id,
    )
    if not ok:
        raise RuntimeError("Export finalize CAS 失败")


async def run_export_dataset_handler(ctx: ExecutionContext) -> None:
    """export_dataset:v1 handler。

    payload: {"export_id": UUID, "dataset_id": UUID, "export_profile_id": UUID,
              "created_by": UUID}
    """
    payload = ctx.payload
    export_id = uuid.UUID(str(payload["export_id"]))
    dataset_id = uuid.UUID(str(payload["dataset_id"]))
    export_profile_id = uuid.UUID(str(payload["export_profile_id"]))
    created_by = uuid.UUID(str(payload["created_by"]))

    await _export_common(
        ctx,
        export_id=export_id,
        source_type="dataset",
        source_id=dataset_id,
        export_profile_id=export_profile_id,
        created_by=created_by,
    )


async def run_export_benchmark_handler(ctx: ExecutionContext) -> None:
    """export_benchmark:v1 handler。

    payload: {"export_id": UUID, "benchmark_id": UUID, "export_profile_id": UUID,
              "created_by": UUID}
    """
    payload = ctx.payload
    export_id = uuid.UUID(str(payload["export_id"]))
    benchmark_id = uuid.UUID(str(payload["benchmark_id"]))
    export_profile_id = uuid.UUID(str(payload["export_profile_id"]))
    created_by = uuid.UUID(str(payload["created_by"]))

    await _export_common(
        ctx,
        export_id=export_id,
        source_type="benchmark",
        source_id=benchmark_id,
        export_profile_id=export_profile_id,
        created_by=created_by,
    )
