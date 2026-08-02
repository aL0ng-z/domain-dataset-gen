import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.dataset import Benchmark, Dataset
from app.models.user import User
from app.schemas.curated import (
    AddToBenchmarkRequest,
    AddToDatasetRequest,
    CuratedItemResponse,
    CuratedItemUpdate,
    CuratedRevisionResponse,
    EvidenceLinkResponse,
)
from app.schemas.dataset import BenchmarkCaseResponse, DatasetItemResponse
from app.services.curated_item_service import CuratedItemService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/curated-items", tags=["curated-items"])


@router.get("/", response_model=PaginatedResponse[CuratedItemResponse])
async def list_curated_items(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    item_status: str | None = Query(None, alias="status"),
    item_type: str | None = None,
):
    service = CuratedItemService(db)
    items, total = await service.list_items(pid, page, page_size, item_status, item_type)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{iid}", response_model=CuratedItemResponse)
async def get_curated_item(
    pid: uuid.UUID,
    iid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    item = await resolver.curated_item(pid, iid)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    return item


@router.patch("/{iid}", response_model=CuratedItemResponse)
async def update_curated_item(
    pid: uuid.UUID,
    iid: uuid.UUID,
    body: CuratedItemUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    updated = await service.update(
        item_id=iid,
        revised_by=current_user.id,
        **body.model_dump(exclude_unset=True),
    )
    return updated


@router.get("/{iid}/revisions", response_model=list[CuratedRevisionResponse])
async def list_revisions(
    pid: uuid.UUID,
    iid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    return await service.list_revisions(iid)


@router.get("/{iid}/evidence-links", response_model=list[EvidenceLinkResponse])
async def list_evidence_links(
    pid: uuid.UUID,
    iid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    service = CuratedItemService(db)
    return await service.list_evidence_links(iid)


@router.post("/{iid}/add-to-dataset", response_model=DatasetItemResponse, status_code=status.HTTP_201_CREATED)
async def add_to_dataset(
    pid: uuid.UUID,
    iid: uuid.UUID,
    body: AddToDatasetRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    # 请求体引用的 dataset 必须属于同一项目（跨项目引用 -> 404）。
    await resolver.ensure_in_project(pid, [(Dataset, body.dataset_id)])
    service = CuratedItemService(db)
    try:
        return await service.add_to_dataset(iid, body.dataset_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/{iid}/add-to-benchmark", response_model=BenchmarkCaseResponse, status_code=status.HTTP_201_CREATED)
async def add_to_benchmark(
    pid: uuid.UUID,
    iid: uuid.UUID,
    body: AddToBenchmarkRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.curated_item(pid, iid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    # 请求体引用的 benchmark 必须属于同一项目。
    await resolver.ensure_in_project(pid, [(Benchmark, body.benchmark_id)])
    service = CuratedItemService(db)
    try:
        return await service.add_to_benchmark(iid, body.benchmark_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
