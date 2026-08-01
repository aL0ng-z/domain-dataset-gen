import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import require_project_member
from app.models.user import User
from app.schemas.export import ExportResponse, SnapshotManifestResponse
from app.services.export_service import ExportService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse
from storage import get_storage_client

router = APIRouter(prefix="/api/projects/{pid}/exports", tags=["exports"])


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
    return PaginatedResponse(items=exports, total=total, page=page, page_size=page_size)


@router.get("/{eid}", response_model=ExportResponse, operation_id="export_get")
async def get_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = ExportService(db)
    export = await service.get_export(eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")
    return export


@router.get("/{eid}/manifest", response_model=SnapshotManifestResponse, operation_id="export_get_manifest")
async def get_manifest(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = ExportService(db)
    export = await service.get_export(eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")

    manifest = await service.get_manifest(export.snapshot_manifest_id)
    if manifest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="快照清单不存在")
    return manifest


@router.get("/{eid}/download", operation_id="export_download")
async def download_export(
    pid: uuid.UUID,
    eid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = ExportService(db)
    export = await service.get_export(eid)
    if export is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="导出记录不存在")

    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key,
        settings.minio_secret_key, settings.minio_secure,
    )
    presigned_url = storage.get_presigned_url(settings.minio_bucket_outputs, export.minio_key)
    return RedirectResponse(url=presigned_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
