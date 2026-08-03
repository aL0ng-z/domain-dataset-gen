import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.config import settings
from app.database import get_db
from app.dependencies import require_project_member
from app.models.export import Export, ExportArtifactSeal, SnapshotManifest
from app.models.user import User
from app.schemas.export import (
    ExportDownloadLinkResponse,
    ExportResponse,
    ExportVerifyResponse,
    SnapshotManifestResponse,
    VerifyDeepItem,
    VerifyShallowResult,
)
from app.services.export_service import ExportService
from domain.enums import UserRole
from domain.manifest import manifest_sha256
from domain.schemas import PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/exports", tags=["exports"])

DOWNLOAD_LINK_TTL_SECONDS = 300


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


async def _prepare_download(pid: uuid.UUID, eid: uuid.UUID, db: AsyncSession) -> tuple[Export, str]:
    """校验导出完整性并返回即时签发的版本固定 URL。"""
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
        return export, presigned_url
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
        settings.minio_bucket_outputs,
        export.minio_key,
        export.object_version_id,
        expires=DOWNLOAD_LINK_TTL_SECONDS,
    )
    return export, presigned_url


@router.get("/{eid}/download", operation_id="export_download")
async def download_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    """仅 completed 且完整性非失败时返回绑定 object_version_id 的短时 307。"""
    _, presigned_url = await _prepare_download(pid, eid, db)
    return RedirectResponse(url=presigned_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post(
    "/{eid}/download-link",
    response_model=ExportDownloadLinkResponse,
    operation_id="export_create_download_link",
)
async def create_download_link(
    pid: uuid.UUID,
    eid: uuid.UUID,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    """Bearer 鉴权后即时签发短期 URL；响应及中间代理不得缓存。"""
    export, presigned_url = await _prepare_download(pid, eid, db)
    response.headers["Cache-Control"] = "no-store"
    object_name = (export.minio_key or f"export-{eid}").rsplit("/", 1)[-1]
    suffix = object_name.rsplit(".", 1)[-1] if "." in object_name else "bin"
    return ExportDownloadLinkResponse(
        url=presigned_url,
        expires_at=datetime.now(UTC) + timedelta(seconds=DOWNLOAD_LINK_TTL_SECONDS),
        filename=f"export-{eid}.{suffix}",
    )


async def _verify_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: AsyncSession,
    deep: bool,
) -> ExportVerifyResponse:
    """导出完整性验证的共享实现。"""
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
            ),
            deep=None,
        )
    if export.status != "completed":
        return ExportVerifyResponse(
            export_id=eid,
            status="pending",
            shallow=VerifyShallowResult(
                db_fields_present=True, version_id_present=False, metadata_ok=None
            ),
            deep=None,
        )

    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )
    db_fields_present = all((
        export.minio_key,
        export.output_sha256,
        export.file_size is not None,
        export.manifest_key,
        export.manifest_object_version_id,
        export.manifest_sha256,
        export.artifact_seal_id,
        export.snapshot_manifest_id,
    ))
    version_id_present = bool(
        export.object_version_id and export.manifest_object_version_id
    )
    seal = await db.get(ExportArtifactSeal, export.artifact_seal_id)
    shallow_ok = False
    metadata_ok = None
    try:
        payload_stat = storage.stat_object_version(
            settings.minio_bucket_outputs, export.minio_key, export.object_version_id
        )
        manifest_stat = storage.stat_object_version(
            settings.minio_bucket_outputs,
            export.manifest_key,
            export.manifest_object_version_id,
        )
        metadata_ok = bool(
            seal
            and payload_stat["size"] == export.file_size == seal.output_size
            and payload_stat["sha256"] == export.output_sha256 == seal.output_sha256
            and manifest_stat["size"] == seal.manifest_size
            and manifest_stat["sha256"] == export.manifest_sha256 == seal.manifest_sha256
        )
        shallow_ok = bool(db_fields_present and version_id_present and metadata_ok)
    except Exception:  # noqa: BLE001
        metadata_ok = False

    deep_items: list[VerifyDeepItem] | None = None
    if deep and export.minio_key and export.object_version_id:
        deep_items = []
        try:
            actual = storage.sha256_object_version(
                settings.minio_bucket_outputs, export.minio_key, export.object_version_id
            )
            deep_items.append(
                VerifyDeepItem(
                    item="payload_sha256",
                    ok=actual == export.output_sha256,
                    detail=f"expected={export.output_sha256} actual={actual}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            deep_items.append(VerifyDeepItem(item="payload_sha256", ok=False, detail=str(exc)))
        if export.manifest_key and export.manifest_object_version_id:
            try:
                mactual = storage.sha256_object_version(
                    settings.minio_bucket_outputs, export.manifest_key, export.manifest_object_version_id
                )
                deep_items.append(
                    VerifyDeepItem(
                        item="manifest_sha256",
                        ok=mactual == export.manifest_sha256,
                        detail=f"expected={export.manifest_sha256} actual={mactual}",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                deep_items.append(VerifyDeepItem(item="manifest_sha256", ok=False, detail=str(exc)))
        snapshot = await db.get(SnapshotManifest, export.snapshot_manifest_id)
        if snapshot is None:
            deep_items.append(
                VerifyDeepItem(
                    item="manifest_canonical_sha256",
                    ok=False,
                    detail="SnapshotManifest 不存在",
                )
            )
        else:
            canonical_hash = manifest_sha256(snapshot.manifest)
            deep_items.append(
                VerifyDeepItem(
                    item="manifest_canonical_sha256",
                    ok=(
                        canonical_hash == snapshot.manifest_sha256 == export.manifest_sha256
                    ),
                    detail=(
                        f"expected={export.manifest_sha256} actual={canonical_hash}"
                    ),
                )
            )

    return ExportVerifyResponse(
        export_id=eid,
        status="verified" if (shallow_ok and (deep_items is None or all(i.ok for i in deep_items))) else "failed",
        shallow=VerifyShallowResult(
            db_fields_present=bool(db_fields_present),
            version_id_present=version_id_present,
            metadata_ok=metadata_ok,
        ),
        deep=deep_items,
    )


@router.post("/{eid}/verify", response_model=ExportVerifyResponse, operation_id="export_verify")
async def verify_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    deep: bool = Query(False),
):
    """执行浅验证；``deep=true`` 时流式重算两个固定对象版本的 hash。"""
    return await _verify_export(pid, eid, db, deep)


@router.get(
    "/{eid}/verify",
    response_model=ExportVerifyResponse,
    operation_id="export_verify_legacy_get",
    deprecated=True,
)
async def verify_export_legacy_get(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
    deep: bool = Query(False),
):
    """兼容旧客户端；新调用应使用 POST。"""
    return await _verify_export(pid, eid, db, deep)
