import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.config import ExportProfile
from app.models.curated import CuratedItem
from app.models.user import User
from app.schemas.dataset import (
    DatasetCreate,
    DatasetItemAdd,
    DatasetItemResponse,
    DatasetResponse,
    DatasetUpdate,
)
from app.schemas.export import ExportRequest, ExportResponse
from app.services.dataset_service import DatasetService
from app.services.idempotency import idempotent_create_task
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/datasets", tags=["datasets"])


@router.post("/", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED, operation_id="dataset_create")
async def create_dataset(
    pid: uuid.UUID,
    body: DatasetCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = DatasetService(db)
    return await service.create_dataset(pid, body.name, body.description, current_user.id)


@router.get("/", response_model=PaginatedResponse[DatasetResponse], operation_id="dataset_list")
async def list_datasets(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    service = DatasetService(db)
    datasets, total = await service.list_datasets(pid, page, page_size)
    return PaginatedResponse(items=datasets, total=total, page=page, page_size=page_size)


@router.get("/{did}", response_model=DatasetResponse, operation_id="dataset_get")
async def get_dataset(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    dataset = await resolver.dataset(pid, did)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    return dataset


@router.patch("/{did}", response_model=DatasetResponse, operation_id="dataset_update")
async def update_dataset(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: DatasetUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    service = DatasetService(db)
    dataset = await service.update_dataset(did, **body.model_dump(exclude_unset=True))
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    return dataset


@router.delete("/{did}", status_code=status.HTTP_204_NO_CONTENT, operation_id="dataset_delete")
async def delete_dataset(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    service = DatasetService(db)
    await service.delete_dataset(did)


@router.get(
    "/{did}/items",
    response_model=PaginatedResponse[DatasetItemResponse],
    operation_id="dataset_list_items",
)
async def list_items(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    service = DatasetService(db)
    items, total = await service.list_items_paginated(did, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{did}/items", response_model=DatasetItemResponse, status_code=status.HTTP_201_CREATED, operation_id="dataset_add_item")
async def add_item(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: DatasetItemAdd,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    # 请求体引用的 curated item 必须属于同一项目。
    await resolver.ensure_in_project(pid, [(CuratedItem, body.curated_item_id)])
    service = DatasetService(db)
    try:
        return await service.add_item(did, body.curated_item_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.delete("/{did}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="dataset_remove_item")
async def remove_item(
    pid: uuid.UUID,
    did: uuid.UUID,
    item_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    service = DatasetService(db)
    if not await service.remove_item(did, item_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集条目不存在")


@router.post("/{did}/export", response_model=ExportResponse, status_code=status.HTTP_201_CREATED, operation_id="dataset_export")
async def export_dataset(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: ExportRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):

    # Verify dataset exists and export profile belongs to the same project
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    await resolver.ensure_in_project(pid, [(ExportProfile, body.export_profile_id)])

    # Create task for tracking
    redis = getattr(request.app.state, "redis", None)
    task_service = TaskService(db, redis)
    export_payload = {
        "dataset_id": str(did),
        "export_profile_id": str(body.export_profile_id),
        "created_by": str(current_user.id),
    }
    if idempotency_key:
        task = await idempotent_create_task(
            db,
            task_service=task_service,
            client_key=idempotency_key,
            project_id=pid,
            task_type="export",
            payload_for_digest=export_payload,
            create=lambda key: task_service.create_task(
                project_id=pid, task_type="export", entity_type="dataset", entity_id=did,
                created_by=current_user.id, payload=export_payload, handler="export_dataset",
                idempotency_key=key,
            ),
        )
    else:
        task = await task_service.create_task(
            project_id=pid, task_type="export", entity_type="dataset", entity_id=did,
            created_by=current_user.id, payload=export_payload, handler="export_dataset",
        )
    await db.commit()

    # Return a placeholder export response — actual export created by runner
    from app.schemas.export import ExportResponse
    return ExportResponse(
        id=task.id,
        project_id=pid,
        dataset_id=did,
        benchmark_id=None,
        export_profile_id=body.export_profile_id,
        format="pending",
        item_count=0,
        snapshot_manifest_id=task.id,  # placeholder until export completes
        created_by=current_user.id,
        created_at=task.created_at,
    )
