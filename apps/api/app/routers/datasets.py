import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.config import ExportProfile
from app.models.curated import CuratedItem
from app.models.user import User
from app.schemas.dataset import (
    CuratedItemSummaryResponse,
    DatasetCreate,
    DatasetDetailResponse,
    DatasetFinalizeRequest,
    DatasetItemAdd,
    DatasetItemDetailResponse,
    DatasetResponse,
    DatasetUpdate,
)
from app.schemas.export import ExportCreatedResponse, ExportRequest
from app.services.composition_policy import CompositionGateError
from app.services.composition_service import CompositionService
from app.services.dataset_service import DatasetService
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/datasets", tags=["datasets"])


def _gate_409(exc: CompositionGateError) -> HTTPException:
    """把资格门禁异常映射为稳定 409 ErrorResponse。"""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": exc.code, "message": str(exc)},
    )


def _dataset_detail(dataset, item_count: int) -> dict:
    """组装 DatasetDetailResponse（T10 §5.1）。"""
    base = DatasetResponse.model_validate(dataset).model_dump()
    return DatasetDetailResponse(
        **base,
        item_count=item_count,
        composition_revision=dataset.composition_revision,
        composition_sha256=dataset.composition_sha256,
        composition_canonicalization_version=dataset.composition_canonicalization_version,
        finalized_revision=dataset.finalized_revision,
        finalized_sha256=dataset.finalized_sha256,
        finalized_canonicalization_version=dataset.finalized_canonicalization_version,
        finalized_by=dataset.finalized_by,
        finalized_at=dataset.finalized_at,
    ).model_dump()


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


@router.get("/{did}", response_model=DatasetDetailResponse, operation_id="dataset_get")
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
    service = DatasetService(db)
    item_count = await service.count_items(did)
    return _dataset_detail(dataset, item_count)


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
    response_model=PaginatedResponse[DatasetItemDetailResponse],
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
    items, total = await service.list_items(did, page, page_size)
    composition_service = CompositionService(db)
    details = await composition_service.build_membership_details("dataset", did, items)
    return PaginatedResponse(items=details, total=total, page=page, page_size=page_size)


@router.get(
    "/{did}/eligible-items",
    response_model=PaginatedResponse[CuratedItemSummaryResponse],
    operation_id="dataset_list_eligible_items",
)
async def list_eligible_items(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    query: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    resolver = ProjectResourceResolver(db)
    if await resolver.dataset(pid, did) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    composition_service = CompositionService(db)
    items, total = await composition_service.list_eligible_items(
        container_type="dataset",
        container_id=did,
        project_id=pid,
        query=query,
        page=page,
        page_size=page_size,
        require_supported=False,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{did}/items", response_model=DatasetItemDetailResponse, status_code=status.HTTP_201_CREATED, operation_id="dataset_add_item")
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
    # 请求体引用的 curated item 必须属于同一项目（跨项目 -> 404）。
    await resolver.ensure_in_project(pid, [(CuratedItem, body.curated_item_id)])
    composition_service = CompositionService(db)
    try:
        membership = await composition_service.add_membership(
            container_type="dataset",
            container_id=did,
            curated_item_id=body.curated_item_id,
            require_supported=False,
        )
    except CompositionGateError as exc:
        raise _gate_409(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "COMPOSITION_MEMBER_EXISTS", "message": "同一 CuratedItem 已在容器中"},
        ) from exc
    details = await composition_service.build_membership_details("dataset", did, [membership])
    return details[0]


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
    composition_service = CompositionService(db)
    try:
        removed = await composition_service.remove_membership(
            container_type="dataset", container_id=did, membership_id=item_id
        )
    except CompositionGateError as exc:
        raise _gate_409(exc) from exc
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集条目不存在")


@router.post("/{did}/finalize", response_model=DatasetDetailResponse, operation_id="dataset_finalize")
async def finalize_dataset(
    pid: uuid.UUID,
    did: uuid.UUID,
    body: DatasetFinalizeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
):
    resolver = ProjectResourceResolver(db)
    dataset = await resolver.dataset(pid, did)
    if dataset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据集不存在")
    composition_service = CompositionService(db)
    try:
        finalized = await composition_service.finalize(
            container_type="dataset",
            container_id=did,
            expected_revision=body.expected_revision,
            expected_sha256=body.expected_sha256,
            reviewer_id=current_user.id,
        )
    except CompositionGateError as exc:
        raise _gate_409(exc) from exc
    item_count = await DatasetService(db).count_items(did)
    return _dataset_detail(finalized, item_count)


@router.post("/{did}/export", response_model=ExportCreatedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="dataset_export")
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

    # T11：创建 queued Export + Task，返回 202（不再伪造 completed）。
    from app.services.export_service import ExportService
    from app.services.export_trigger import create_export_trigger

    redis = getattr(request.app.state, "redis", None)
    task_service = TaskService(db, redis)
    fingerprint = ExportService.build_request_fingerprint(
        expected_source_revision=body.expected_source_revision,
        expected_source_sha256=body.expected_source_sha256,
        export_profile_id=body.export_profile_id,
    )
    result = await create_export_trigger(
        db=db,
        redis=redis,
        task_service=task_service,
        request_fingerprint=fingerprint,
        project_id=pid,
        source_type="dataset",
        source_id=did,
        export_profile_id=body.export_profile_id,
        created_by=current_user.id,
        expected_source_revision=body.expected_source_revision,
        expected_source_sha256=body.expected_source_sha256,
        idempotency_key=idempotency_key,
        handler="export_dataset",
    )
    await db.commit()
    return ExportCreatedResponse(**result)
