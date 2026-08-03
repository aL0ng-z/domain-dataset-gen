"""T11：导出触发共享逻辑（dataset/benchmark 复用）。

任务卡 §7：``POST .../export`` 返回 ``202 {export_id, task_id, status:"queued"}``，
不再伪造 ``snapshot_manifest_id=task.id``。请求为
``{export_profile_id, expected_source_revision, expected_source_sha256}`` 并支持
``Idempotency-Key``；source 非 finalized、revision/hash 不符、存在未批准/证据不完整
条目时返回稳定 409。
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import ExportProfile
from app.models.dataset import Benchmark, Dataset
from app.models.export import Export
from app.services.export_service import ExportService
from app.services.idempotency import idempotent_create_task
from app.services.task_service import TaskService
from domain.schemas import ErrorResponse

#: 稳定 409 错误 code（与 domain.schemas ERROR_CODES 对齐）。
EXPORT_SOURCE_NOT_FINALIZED = "EXPORT_SOURCE_NOT_FINALIZED"
EXPORT_REVISION_CONFLICT = "EXPORT_REVISION_CONFLICT"
EXPORT_PROVENANCE_GAP = "EXPORT_PROVENANCE_GAP"


def _export_409(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=ErrorResponse(code=code, message=message).model_dump(),
    )


async def _verify_source_consistent(
    db: AsyncSession,
    *,
    source_type: str,
    source_id: uuid.UUID,
    expected_revision: int,
    expected_sha256: str,
) -> Dataset | Benchmark:
    """校验 source 已 finalize 且 revision/hash 与请求一致。

    非 finalized -> EXPORT_SOURCE_NOT_FINALIZED；revision/hash 不符 ->
    EXPORT_REVISION_CONFLICT。
    """
    model = Dataset if source_type == "dataset" else Benchmark
    row = (await db.execute(select(model).where(model.id == source_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="来源不存在")
    if row.status != "finalized":
        raise _export_409(EXPORT_SOURCE_NOT_FINALIZED, "来源未 finalize，禁止导出")
    if row.composition_revision != expected_revision or row.composition_sha256 != expected_sha256:
        raise _export_409(
            EXPORT_REVISION_CONFLICT,
            "composition revision/hash 已过期，请刷新后重新确认",
        )
    return row


async def create_export_trigger(
    *,
    db: AsyncSession,
    redis,
    task_service: TaskService,
    request_fingerprint: str,
    project_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    export_profile_id: uuid.UUID,
    created_by: uuid.UUID,
    expected_source_revision: int,
    expected_source_sha256: str,
    idempotency_key: str | None,
    handler: str,
) -> dict:
    """创建 queued Export + Task，返回 202 响应体。

    幂等：同 key 且请求摘要一致返回既有 Export；同 key 不同摘要 409。
    """
    # 1. source 一致性校验（finalized + revision/hash）。
    await _verify_source_consistent(
        db,
        source_type=source_type,
        source_id=source_id,
        expected_revision=expected_source_revision,
        expected_sha256=expected_source_sha256,
    )
    profile = (
        await db.execute(select(ExportProfile).where(ExportProfile.id == export_profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出配置不存在")

    # 2. 幂等创建 Task（同 key/同摘要复用；不同摘要 409）。
    export_payload = {
        "export_id": "",  # 由 Export 创建后填充
        "source_type": source_type,
        "source_id": str(source_id),
        "export_profile_id": str(export_profile_id),
        "created_by": str(created_by),
        "expected_source_revision": expected_source_revision,
        "expected_source_sha256": expected_source_sha256,
    }

    export_service = ExportService(db)
    # 先检查幂等键：若已有同 key Task 且已完成/进行中，返回其关联 Export。
    existing_export = None
    if idempotency_key:
        existing = await _find_export_by_client_key(db, project_id, idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise _export_409("IDEMPOTENCY_KEY_REUSED", "幂等键已被不同的请求复用")
            existing_export = existing

    if existing_export is not None:
        return {
            "export_id": str(existing_export.id),
            "task_id": str(existing_export.task_id) if existing_export.task_id else "",
            "status": existing_export.status,
        }

    # 3. 创建 queued Export（先不绑定 task，task 创建后回填）。
    export = await export_service.create_export_request(
        project_id=project_id,
        dataset_id=source_id if source_type == "dataset" else None,
        benchmark_id=source_id if source_type == "benchmark" else None,
        export_profile_id=export_profile_id,
        created_by=created_by,
        request_fingerprint=request_fingerprint,
        profile_snapshot=export_service.build_profile_snapshot(profile),
        task_id=None,
    )
    await db.flush()

    # 4. 创建 Task（payload 携带 export_id）。
    export_payload["export_id"] = str(export.id)
    if idempotency_key:
        task = await idempotent_create_task(
            db,
            task_service=task_service,
            client_key=idempotency_key,
            project_id=project_id,
            task_type="export",
            payload_for_digest=export_payload,
            create=lambda key: task_service.create_task(
                project_id=project_id, task_type="export",
                entity_type=source_type, entity_id=source_id,
                created_by=created_by, payload=export_payload, handler=handler,
                idempotency_key=key,
            ),
        )
    else:
        task = await task_service.create_task(
            project_id=project_id, task_type="export",
            entity_type=source_type, entity_id=source_id,
            created_by=created_by, payload=export_payload, handler=handler,
        )
    await db.flush()

    # 5. 回填 Export.task_id。
    export.task_id = task.id
    export.updated_at = task.created_at
    await db.flush()

    return {
        "export_id": str(export.id),
        "task_id": str(task.id),
        "status": "queued",
    }


async def _find_export_by_client_key(
    db: AsyncSession, project_id: uuid.UUID, client_key: str
) -> Export | None:
    """按客户端 key 前缀查找关联 Export（经 Task.idempotency_key 反查）。"""
    from app.models.task import Task

    prefix = f"{client_key}:"
    result = await db.execute(
        select(Export)
        .join(Task, Task.id == Export.task_id)
        .where(
            Export.project_id == project_id,
            Export.is_legacy.is_(False),
            Task.idempotency_key.like(f"{prefix}%"),
        )
        .order_by(Export.created_at.desc())
    )
    return result.scalars().first()
