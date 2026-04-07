"""Export worker: formats curated items for dataset/benchmark export and uploads to MinIO."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.config import ExportProfile
from app.models.curated import CuratedItem, EvidenceLink
from app.models.dataset import Benchmark, BenchmarkCase, Dataset, DatasetItem
from app.models.export import Export, SnapshotManifest
from app.services.export_service import ExportService
from app.services.task_service import TaskService
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

FORMAT_EXTENSIONS = {
    "sft_jsonl": "jsonl",
    "qa_json": "json",
    "messages": "json",
    "alpaca": "json",
    "sharegpt": "json",
    "benchmark_json": "json",
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
# Main export functions
# ---------------------------------------------------------------------------

async def run_export_dataset(
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    export_profile_id: uuid.UUID,
    created_by: uuid.UUID,
    db: AsyncSession,
    redis=None,
) -> None:
    """Export a dataset's curated items in the specified format."""
    task_service = TaskService(db, redis)
    export_service = ExportService(db)
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        # Load export profile
        profile = (
            await db.execute(select(ExportProfile).where(ExportProfile.id == export_profile_id))
        ).scalar_one()
        fmt = profile.format

        if fmt not in FORMAT_HANDLERS:
            raise ValueError(f"不支持的导出格式: {fmt}")

        # Load dataset and items
        dataset = (
            await db.execute(select(Dataset).where(Dataset.id == dataset_id))
        ).scalar_one()

        await task_service.update_status(task_id, "processing", progress=20)

        items_result = await db.execute(
            select(DatasetItem).where(DatasetItem.dataset_id == dataset_id).order_by(DatasetItem.ordinal)
        )
        dataset_items = list(items_result.scalars().all())

        if not dataset_items:
            raise ValueError("数据集为空，无可导出的条目")

        # Load curated items
        curated_ids = [di.curated_item_id for di in dataset_items]
        curated_result = await db.execute(
            select(CuratedItem).where(CuratedItem.id.in_(curated_ids))
        )
        curated_map = {ci.id: ci for ci in curated_result.scalars().all()}

        await task_service.update_status(task_id, "processing", progress=40)

        # Build item dicts with evidence
        formatted_items = []
        for di in dataset_items:
            ci = curated_map.get(di.curated_item_id)
            if ci is None:
                continue
            item_dict = {"content": ci.content, "curated_item_id": str(ci.id)}
            # Attach evidence text if available
            ev_result = await db.execute(
                select(EvidenceLink).where(EvidenceLink.curated_item_id == ci.id)
            )
            evidence_links = list(ev_result.scalars().all())
            if evidence_links:
                item_dict["evidence_text"] = "; ".join(
                    el.quote_text for el in evidence_links if el.quote_text
                )
            formatted_items.append(item_dict)

        await task_service.update_status(task_id, "processing", progress=60)

        # Format output
        handler = FORMAT_HANDLERS[fmt]
        output_text = handler(formatted_items)

        # Build snapshot manifest
        manifest_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "dataset",
            "dataset_id": str(dataset_id),
            "dataset_name": dataset.name,
            "export_profile_id": str(export_profile_id),
            "export_format": fmt,
            "item_count": len(formatted_items),
            "curated_item_ids": [str(ci_id) for ci_id in curated_ids],
        }
        snapshot = await export_service.create_snapshot_manifest(manifest_data)

        await task_service.update_status(task_id, "processing", progress=80)

        # Upload to MinIO
        ext = FORMAT_EXTENSIONS.get(fmt, "json")
        content_type = FORMAT_CONTENT_TYPES.get(fmt, "application/json")
        minio_key = f"{project_id}/exports/dataset_{dataset_id}.{ext}"
        storage.upload_file(
            settings.minio_bucket_outputs, minio_key,
            output_text.encode("utf-8"), content_type,
        )

        # Create export record
        await export_service.create_export(
            project_id=project_id,
            dataset_id=dataset_id,
            benchmark_id=None,
            export_profile_id=export_profile_id,
            minio_key=minio_key,
            format=fmt,
            item_count=len(formatted_items),
            snapshot_manifest_id=snapshot.id,
            created_by=created_by,
        )

        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        await task_service.update_status(task_id, "failed", error_message=str(e))


async def run_export_benchmark(
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    benchmark_id: uuid.UUID,
    export_profile_id: uuid.UUID,
    created_by: uuid.UUID,
    db: AsyncSession,
    redis=None,
) -> None:
    """Export a benchmark's curated items in the specified format."""
    task_service = TaskService(db, redis)
    export_service = ExportService(db)
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        # Load export profile
        profile = (
            await db.execute(select(ExportProfile).where(ExportProfile.id == export_profile_id))
        ).scalar_one()
        fmt = profile.format

        if fmt not in FORMAT_HANDLERS:
            raise ValueError(f"不支持的导出格式: {fmt}")

        # Load benchmark and cases
        benchmark = (
            await db.execute(select(Benchmark).where(Benchmark.id == benchmark_id))
        ).scalar_one()

        await task_service.update_status(task_id, "processing", progress=20)

        cases_result = await db.execute(
            select(BenchmarkCase).where(BenchmarkCase.benchmark_id == benchmark_id).order_by(BenchmarkCase.ordinal)
        )
        benchmark_cases = list(cases_result.scalars().all())

        if not benchmark_cases:
            raise ValueError("基准集为空，无可导出的案例")

        # Load curated items
        curated_ids = [bc.curated_item_id for bc in benchmark_cases]
        curated_result = await db.execute(
            select(CuratedItem).where(CuratedItem.id.in_(curated_ids))
        )
        curated_map = {ci.id: ci for ci in curated_result.scalars().all()}

        await task_service.update_status(task_id, "processing", progress=40)

        # Build item dicts with evidence
        formatted_items = []
        for bc in benchmark_cases:
            ci = curated_map.get(bc.curated_item_id)
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

        await task_service.update_status(task_id, "processing", progress=60)

        # Format output
        handler = FORMAT_HANDLERS[fmt]
        output_text = handler(formatted_items)

        # Build snapshot manifest
        manifest_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "benchmark",
            "benchmark_id": str(benchmark_id),
            "benchmark_name": benchmark.name,
            "export_profile_id": str(export_profile_id),
            "export_format": fmt,
            "item_count": len(formatted_items),
            "curated_item_ids": [str(ci_id) for ci_id in curated_ids],
        }
        snapshot = await export_service.create_snapshot_manifest(manifest_data)

        await task_service.update_status(task_id, "processing", progress=80)

        # Upload to MinIO
        ext = FORMAT_EXTENSIONS.get(fmt, "json")
        content_type = FORMAT_CONTENT_TYPES.get(fmt, "application/json")
        minio_key = f"{project_id}/exports/benchmark_{benchmark_id}.{ext}"
        storage.upload_file(
            settings.minio_bucket_outputs, minio_key,
            output_text.encode("utf-8"), content_type,
        )

        # Create export record
        await export_service.create_export(
            project_id=project_id,
            dataset_id=None,
            benchmark_id=benchmark_id,
            export_profile_id=export_profile_id,
            minio_key=minio_key,
            format=fmt,
            item_count=len(formatted_items),
            snapshot_manifest_id=snapshot.id,
            created_by=created_by,
        )

        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        await task_service.update_status(task_id, "failed", error_message=str(e))
