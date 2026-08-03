import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.config import settings
from app.database import get_db
from app.dependencies import require_project_member
from app.models.export import Export
from app.models.user import User
from app.schemas.export import (
    ExportResponse,
    ExportVerifyResponse,
    SnapshotManifestResponse,
    VerifyDeepItem,
    VerifyShallowResult,
)
from app.services.export_service import ExportService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/exports", tags=["exports"])


def _export_response(export: Export) -> dict:
    """组装 ExportResponse（含 integrity_status 派生）。"""
    return {
        "id": export.id,
        "project_id": export.project_id,
        "dataset_id": export.dataset_id,
        "benchmark_id": export.benchmark_id,
        "source_type": export.source_type,
        "export_profile_id": export.export_profile_id,
        "format": export.format or "",
        "status": export.status,
        "item_count": export.item_count,
        "output_sha256": export.output_sha256,
        "file_size": export.file_size,
        "integrity_status": "unverified_legacy" if export.is_legacy else (
            "verified" if export.status == "completed" else "pending"
        ),
        "task_id": export.task_id,
        "retry_count": export.retry_count,
        "error_code": export.error_code,
        "error_message": export.error_message,
        "is_legacy": export.is_legacy,
        "created_by": export.created_by,
        "created_at": export.created_at,
        "completed_at": export.completed_at,
        "snapshot_manifest_id": export.snapshot_manifest_id,
    }


@router.get("/", response_model=PaginatedResponse[ExportResponse], operation_id="export_list")
async def list_exports(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    service = ExportService(db)
    exports, total = await service.list_exports(pid, page, page_size)
    return PaginatedResponse(items=[_export_response(e) for e in exports], total=total, page=page, page_size=page_size)


@router.get("/{eid}", response_model=ExportResponse, operation_id="export_get")
async def get_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    export = await resolver.export(pid, eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")
    return _export_response(export)


@router.get("/{eid}/manifest", response_model=SnapshotManifestResponse, operation_id="export_get_manifest")
async def get_manifest(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    export = await resolver.export(pid, eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")
    service = ExportService(db)
    manifest = await service.get_manifest_by_export(eid)
    if manifest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="快照清单不存在")
    integrity_status = "unverified_legacy" if manifest.is_legacy else "verified"
    return SnapshotManifestResponse(
        id=manifest.id,
        export_id=manifest.export_id,
        manifest=manifest.manifest,
        schema_version=manifest.schema_version,
        canonicalization_version=manifest.canonicalization_version,
        manifest_sha256=None if manifest.is_legacy else manifest.manifest_sha256,
        integrity_status=integrity_status,
        sealed_at=manifest.sealed_at,
        created_at=manifest.created_at,
    )


@router.get("/{eid}/download", operation_id="export_download")
async def download_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    """仅 completed 且完整性非失败时返回绑定 object_version_id 的短时 307。

    签发前 HEAD 校验 size/hash metadata；不符返回 409 EXPORT_INTEGRITY_ERROR。
    """
    resolver = ProjectResourceResolver(db)
    export = await resolver.export(pid, eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")
    if export.is_legacy:
        # legacy 旧记录：无对象 version 绑定，仅当仍可访问原始 key 时提供下载（不伪造完整性）。
        if not export.minio_key:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "EXPORT_INTEGRITY_ERROR", "message": "legacy 导出无法验证完整性"})
        storage = get_storage_client(
            settings.minio_endpoint, settings.minio_access_key,
            settings.minio_secret_key, settings.minio_secure,
        )
        presigned_url = storage.get_presigned_url(settings.minio_bucket_outputs, export.minio_key)
        return RedirectResponse(url=presigned_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    if export.status != "completed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "CONFLICT", "message": "导出未完成"})
    if export.object_version_id is None or export.minio_key is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "EXPORT_INTEGRITY_ERROR", "message": "导出对象版本缺失"})

    # 签发前 HEAD 校验 size/hash metadata。
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )
    try:
        stat = storage.stat_object_version(
            settings.minio_bucket_outputs, export.minio_key, export.object_version_id
        )
    except Exception:  # noqa: BLE001 - 对象不可读视为完整性失败
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EXPORT_INTEGRITY_ERROR", "message": "对象版本不可读"},
        ) from None
    if stat["size"] != export.file_size or (export.output_sha256 and stat["sha256"] != export.output_sha256):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "EXPORT_INTEGRITY_ERROR", "message": "对象 metadata 与记录不符"},
        )

    presigned_url = storage.presign_object_version(
        settings.minio_bucket_outputs, export.minio_key, export.object_version_id, expires=300,
    )
    return RedirectResponse(url=presigned_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/{eid}/verify", response_model=ExportVerifyResponse, operation_id="export_verify")
async def verify_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    deep: bool = Query(False),
):
    """导出完整性验证。

    - 浅验证：核对 DB 产物字段、version id 与对象 metadata。
    - ``deep=true``：流式重算 payload 与 manifest hash，返回逐项结果。
    """
    resolver = ProjectResourceResolver(db)
    export = await resolver.export(pid, eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")

    if export.is_legacy:
        return ExportVerifyResponse(
            export_id=eid,
            status="unverified_legacy",
            shallow=VerifyShallowResult(
                db_fields_present=bool(export.minio_key),
                version_id_present=False,
                metadata_ok=None,
            ).model_dump(),
            deep=None,
        )
    if export.status != "completed":
        return ExportVerifyResponse(
            export_id=eid,
            status="pending",
            shallow=VerifyShallowResult(
                db_fields_present=True, version_id_present=False, metadata_ok=None
            ).model_dump(),
            deep=None,
        )

    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )
    shallow_ok = False
    metadata_ok = None
    try:
        stat = storage.stat_object_version(
            settings.minio_bucket_outputs, export.minio_key, export.object_version_id
        )
        metadata_ok = stat["size"] == export.file_size and stat["sha256"] == export.output_sha256
        shallow_ok = export.object_version_id is not None and metadata_ok
    except Exception:  # noqa: BLE001
        metadata_ok = False

    deep_items: list[VerifyDeepItem] | None = None
    if deep and export.minio_key and export.object_version_id:
        deep_items = []
        try:
            data = storage.download_object_version(
                settings.minio_bucket_outputs, export.minio_key, export.object_version_id
            )
            actual = hashlib.sha256(data).hexdigest()
            deep_items.append(
                VerifyDeepItem(
                    item="payload_sha256",
                    ok=actual == export.output_sha256,
                    detail=f"expected={export.output_sha256} actual={actual}",
                ).model_dump()
            )
        except Exception as exc:  # noqa: BLE001
            deep_items.append(VerifyDeepItem(item="payload_sha256", ok=False, detail=str(exc)).model_dump())
        if export.manifest_key and export.manifest_object_version_id:
            try:
                mdata = storage.download_object_version(
                    settings.minio_bucket_outputs, export.manifest_key, export.manifest_object_version_id
                )
                mactual = hashlib.sha256(mdata).hexdigest()
                deep_items.append(
                    VerifyDeepItem(
                        item="manifest_sha256",
                        ok=mactual == export.manifest_sha256,
                        detail=f"expected={export.manifest_sha256} actual={mactual}",
                    ).model_dump()
                )
            except Exception as exc:  # noqa: BLE001
                deep_items.append(VerifyDeepItem(item="manifest_sha256", ok=False, detail=str(exc)).model_dump())

    return ExportVerifyResponse(
        export_id=eid,
        status="verified" if (shallow_ok and (deep_items is None or all(i["ok"] for i in deep_items))) else "failed",
        shallow=VerifyShallowResult(
            db_fields_present=True, version_id_present=True, metadata_ok=metadata_ok
        ).model_dump(),
        deep=deep_items,
    )
