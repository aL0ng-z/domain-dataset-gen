"""Export handlers: formats curated items for dataset/benchmark export and uploads to MinIO.

- export_dataset:v1 / export_benchmark:v1 handler。
- payload 只存资源 id 与 format 选项，不存导出内容/URL。
- 上传 MinIO 与创建 Export/SnapshotManifest 在 handler 返回后由 runner 与
  completed 转换原子提交（取消/失败时整体回滚，不留下正式导出产物）。
"""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.models.config import ExportProfile
from app.models.curated import CuratedItem, EvidenceLink
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem
from app.services.export_service import ExportService
from app.workers.execution import ExecutionContext
from storage import get_storage_client

# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def _format_sft_jsonl(items: list[dict]) -> str:
    """One JSON object per line: {"instruction": ..., "input": ..., "output": ...}"""
    lines = []
    for item in items:
        content = item["content"]
        record = {
            "instruction": content.get("instruction", content.get("question", "")),
            "input": content.get("input", ""),
            "output": content.get("output", content.get("answer", "")),
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines)


def _format_qa_json(items: list[dict]) -> str:
    """JSON array: [{"question": ..., "answer": ...}]"""
    records = []
    for item in items:
        content = item["content"]
        records.append({
            "question": content.get("question", content.get("instruction", "")),
            "answer": content.get("answer", content.get("output", "")),
        })
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_messages(items: list[dict]) -> str:
    """JSON array: [{"messages": [{"role":"system",...},{"role":"user",...},{"role":"assistant",...}]}]"""
    records = []
    for item in items:
        content = item["content"]
        messages = [
            {"role": "system", "content": content.get("system", "你是一个压气机设计领域的专家。")},
            {"role": "user", "content": content.get("instruction", content.get("question", ""))},
            {"role": "assistant", "content": content.get("output", content.get("answer", ""))},
        ]
        records.append({"messages": messages})
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_alpaca(items: list[dict]) -> str:
    """JSON array: [{"instruction": ..., "input": ..., "output": ...}]"""
    records = []
    for item in items:
        content = item["content"]
        records.append({
            "instruction": content.get("instruction", content.get("question", "")),
            "input": content.get("input", ""),
            "output": content.get("output", content.get("answer", "")),
        })
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_sharegpt(items: list[dict]) -> str:
    """JSON array: [{"conversations": [{"from":"human",...},{"from":"gpt",...}]}]"""
    records = []
    for item in items:
        content = item["content"]
        conversations = [
            {"from": "human", "value": content.get("instruction", content.get("question", ""))},
            {"from": "gpt", "value": content.get("output", content.get("answer", ""))},
        ]
        records.append({"conversations": conversations})
    return json.dumps(records, ensure_ascii=False, indent=2)


def _format_benchmark_json(items: list[dict]) -> str:
    """JSON array: [{"question": ..., "reference_answer": ..., "evidence": ...}]"""
    records = []
    for item in items:
        content = item["content"]
        records.append({
            "question": content.get("question", content.get("instruction", "")),
            "reference_answer": content.get("reference_answer", content.get("answer", content.get("output", ""))),
            "evidence": content.get("evidence", item.get("evidence_text", "")),
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


# ---------------------------------------------------------------------------
# 共享导出逻辑
# ---------------------------------------------------------------------------


async def _build_formatted_items(db, curated_ids: list[uuid.UUID], ctx: ExecutionContext) -> list[dict]:
    """加载 curated items 并附加 evidence；批次 checkpoint。"""
    curated_result = await db.execute(
        select(CuratedItem).where(CuratedItem.id.in_(curated_ids))
    )
    curated_map = {ci.id: ci for ci in curated_result.scalars().all()}
    await ctx.checkpoint()

    formatted_items = []
    for ci_id in curated_ids:
        ci = curated_map.get(ci_id)
        if ci is None:
            continue
        item_dict = {"content": ci.content, "curated_item_id": str(ci.id)}
        ev_result = await db.execute(
            select(EvidenceLink).where(EvidenceLink.curated_item_id == ci.id)
        )
        evidence_links = list(ev_result.scalars().all())
        if evidence_links:
            item_dict["evidence_text"] = "; ".join(
                el.quote_text for el in evidence_links if el.quote_text
            )
        formatted_items.append(item_dict)
    return formatted_items


async def _export_common(
    ctx: ExecutionContext,
    *,
    export_profile_id: uuid.UUID,
    manifest_source: dict,
    curated_ids: list[uuid.UUID],
    minio_key: str,
    created_by: uuid.UUID,
) -> None:
    """导出公共流程：格式化 -> 快照清单 -> 上传 MinIO -> 创建 Export 记录。"""
    db = ctx.db
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )
    export_service = ExportService(db)

    profile = (
        await db.execute(select(ExportProfile).where(ExportProfile.id == export_profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise RuntimeError("导出配置不存在")
    fmt = profile.format
    if fmt not in FORMAT_HANDLERS:
        raise RuntimeError(f"不支持的导出格式: {fmt}")
    await ctx.checkpoint()

    formatted_items = await _build_formatted_items(db, curated_ids, ctx)
    if not formatted_items:
        raise RuntimeError("没有可导出的条目")

    output_text = FORMAT_HANDLERS[fmt](formatted_items)

    # Build snapshot manifest
    manifest_data = {
        "exported_at": datetime.now(UTC).isoformat(),
        **manifest_source,
        "export_profile_id": str(export_profile_id),
        "export_format": fmt,
        "item_count": len(formatted_items),
        "curated_item_ids": [str(ci_id) for ci_id in curated_ids],
    }
    snapshot = await export_service.create_snapshot_manifest(manifest_data)
    await ctx.checkpoint()

    # Upload to MinIO
    content_type = FORMAT_CONTENT_TYPES.get(fmt, "application/json")
    storage.upload_file(
        settings.minio_bucket_outputs, minio_key,
        output_text.encode("utf-8"), content_type,
    )
    await ctx.checkpoint()

    # Create export record
    await export_service.create_export(
        project_id=ctx.project_id,
        dataset_id=manifest_source.get("dataset_id"),
        benchmark_id=manifest_source.get("benchmark_id"),
        export_profile_id=export_profile_id,
        minio_key=minio_key,
        format=fmt,
        item_count=len(formatted_items),
        snapshot_manifest_id=snapshot.id,
        created_by=created_by,
    )


async def run_export_dataset_handler(ctx: ExecutionContext) -> None:
    """export_dataset:v1 handler。

    payload: {"dataset_id": UUID, "export_profile_id": UUID, "created_by": UUID}
    """
    payload = ctx.payload
    dataset_id = uuid.UUID(str(payload["dataset_id"]))
    export_profile_id = uuid.UUID(str(payload["export_profile_id"]))
    created_by = uuid.UUID(str(payload["created_by"]))
    db = ctx.db

    # 项目链复核：export profile 与 dataset 必须属于 project_id。
    from app.authz import ProjectChainError, verify_project_chain

    await verify_project_chain(
        db,
        ctx.project_id,
        [(ExportProfile, export_profile_id), (Dataset, dataset_id)],
        detail="数据集导出项目链不一致",
    )
    dataset = (await db.execute(select(Dataset).where(Dataset.id == dataset_id))).scalar_one_or_none()
    if dataset is None:
        raise ProjectChainError("数据集不存在")
    await ctx.checkpoint()

    items_result = await db.execute(
        select(DatasetItem).where(DatasetItem.dataset_id == dataset_id).order_by(DatasetItem.ordinal)
    )
    dataset_items = list(items_result.scalars().all())
    curated_ids = [di.curated_item_id for di in dataset_items]

    minio_key = f"{ctx.project_id}/exports/dataset_{dataset_id}.jsonl"
    await _export_common(
        ctx,
        export_profile_id=export_profile_id,
        manifest_source={
            "source_type": "dataset",
            "dataset_id": str(dataset_id),
            "dataset_name": dataset.name,
        },
        curated_ids=curated_ids,
        minio_key=minio_key,
        created_by=created_by,
    )


async def run_export_benchmark_handler(ctx: ExecutionContext) -> None:
    """export_benchmark:v1 handler。

    payload: {"benchmark_id": UUID, "export_profile_id": UUID, "created_by": UUID}
    """
    payload = ctx.payload
    benchmark_id = uuid.UUID(str(payload["benchmark_id"]))
    export_profile_id = uuid.UUID(str(payload["export_profile_id"]))
    created_by = uuid.UUID(str(payload["created_by"]))
    db = ctx.db

    # 项目链复核：export profile 与 benchmark 必须属于 project_id。
    from app.authz import ProjectChainError, verify_project_chain

    await verify_project_chain(
        db,
        ctx.project_id,
        [(ExportProfile, export_profile_id), (Benchmark, benchmark_id)],
        detail="基准集导出项目链不一致",
    )
    benchmark = (await db.execute(select(Benchmark).where(Benchmark.id == benchmark_id))).scalar_one_or_none()
    if benchmark is None:
        raise ProjectChainError("基准集不存在")
    await ctx.checkpoint()

    cases_result = await db.execute(
        select(BenchmarkCase).where(BenchmarkCase.benchmark_id == benchmark_id).order_by(BenchmarkCase.ordinal)
    )
    benchmark_cases = list(cases_result.scalars().all())
    curated_ids = [bc.curated_item_id for bc in benchmark_cases]

    minio_key = f"{ctx.project_id}/exports/benchmark_{benchmark_id}.jsonl"
    await _export_common(
        ctx,
        export_profile_id=export_profile_id,
        manifest_source={
            "source_type": "benchmark",
            "benchmark_id": str(benchmark_id),
            "benchmark_name": benchmark.name,
        },
        curated_ids=curated_ids,
        minio_key=minio_key,
        created_by=created_by,
    )
