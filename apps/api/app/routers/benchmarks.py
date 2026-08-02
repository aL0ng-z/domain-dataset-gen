import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.config import ExportProfile
from app.models.curated import CuratedItem
from app.models.user import User
from app.schemas.dataset import (
    BenchmarkCaseAdd,
    BenchmarkCaseResponse,
    BenchmarkCreate,
    BenchmarkResponse,
    BenchmarkUpdate,
)
from app.schemas.export import ExportRequest, ExportResponse
from app.services.benchmark_service import BenchmarkService
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/benchmarks", tags=["benchmarks"])


@router.post("/", response_model=BenchmarkResponse, status_code=status.HTTP_201_CREATED, operation_id="benchmark_create")
async def create_benchmark(
    pid: uuid.UUID,
    body: BenchmarkCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = BenchmarkService(db)
    return await service.create_benchmark(pid, body.name, body.description, current_user.id)


@router.get("/", response_model=PaginatedResponse[BenchmarkResponse], operation_id="benchmark_list")
async def list_benchmarks(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    service = BenchmarkService(db)
    benchmarks, total = await service.list_benchmarks(pid, page, page_size)
    return PaginatedResponse(items=benchmarks, total=total, page=page, page_size=page_size)


@router.get("/{bid}", response_model=BenchmarkResponse, operation_id="benchmark_get")
async def get_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    benchmark = await resolver.benchmark(pid, bid)
    if benchmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    return benchmark


@router.patch("/{bid}", response_model=BenchmarkResponse, operation_id="benchmark_update")
async def update_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: BenchmarkUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    service = BenchmarkService(db)
    benchmark = await service.update_benchmark(bid, **body.model_dump(exclude_unset=True))
    if benchmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    return benchmark


@router.delete("/{bid}", status_code=status.HTTP_204_NO_CONTENT, operation_id="benchmark_delete")
async def delete_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    service = BenchmarkService(db)
    await service.delete_benchmark(bid)


@router.get(
    "/{bid}/cases",
    response_model=PaginatedResponse[BenchmarkCaseResponse],
    operation_id="benchmark_list_cases",
)
async def list_cases(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    service = BenchmarkService(db)
    items, total = await service.list_cases_paginated(bid, page, page_size)
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{bid}/cases", response_model=BenchmarkCaseResponse, status_code=status.HTTP_201_CREATED, operation_id="benchmark_add_case")
async def add_case(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: BenchmarkCaseAdd,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    # 请求体引用的 curated item 必须属于同一项目。
    await resolver.ensure_in_project(pid, [(CuratedItem, body.curated_item_id)])
    service = BenchmarkService(db)
    try:
        return await service.add_case(bid, body.curated_item_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.delete("/{bid}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="benchmark_remove_case")
async def remove_case(
    pid: uuid.UUID,
    bid: uuid.UUID,
    case_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    service = BenchmarkService(db)
    if not await service.remove_case(bid, case_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准案例不存在")


@router.post("/{bid}/export", response_model=ExportResponse, status_code=status.HTTP_201_CREATED, operation_id="benchmark_export")
async def export_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: ExportRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):

    # Verify benchmark exists and export profile belongs to the same project
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    await resolver.ensure_in_project(pid, [(ExportProfile, body.export_profile_id)])

    # Create task for tracking
    redis = getattr(request.app.state, "redis", None)
    task_service = TaskService(db, redis)
    task = await task_service.create_task(
        project_id=pid,
        task_type="export",
        entity_type="benchmark",
        entity_id=bid,
        created_by=current_user.id,
        payload={
            "benchmark_id": str(bid),
            "export_profile_id": str(body.export_profile_id),
            "created_by": str(current_user.id),
        },
        handler="export_benchmark",
    )
    await db.commit()

    from app.schemas.export import ExportResponse
    return ExportResponse(
        id=task.id,
        project_id=pid,
        dataset_id=None,
        benchmark_id=bid,
        export_profile_id=body.export_profile_id,
        format="pending",
        item_count=0,
        snapshot_manifest_id=task.id,
        created_by=current_user.id,
        created_at=task.created_at,
    )
