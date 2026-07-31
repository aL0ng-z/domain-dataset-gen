import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_project_member
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


@router.post("/", response_model=BenchmarkResponse, status_code=status.HTTP_201_CREATED)
async def create_benchmark(
    pid: uuid.UUID,
    body: BenchmarkCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = BenchmarkService(db)
    return await service.create_benchmark(pid, body.name, body.description, current_user.id)


@router.get("/", response_model=PaginatedResponse[BenchmarkResponse])
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


@router.get("/{bid}", response_model=BenchmarkResponse)
async def get_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = BenchmarkService(db)
    benchmark = await service.get_benchmark(bid)
    if benchmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    return benchmark


@router.patch("/{bid}", response_model=BenchmarkResponse)
async def update_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: BenchmarkUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = BenchmarkService(db)
    benchmark = await service.update_benchmark(bid, **body.model_dump(exclude_unset=True))
    if benchmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    return benchmark


@router.delete("/{bid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = BenchmarkService(db)
    if not await service.delete_benchmark(bid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")


@router.get("/{bid}/cases", response_model=list[BenchmarkCaseResponse])
async def list_cases(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    service = BenchmarkService(db)
    return await service.list_cases(bid)


@router.post("/{bid}/cases", response_model=BenchmarkCaseResponse, status_code=status.HTTP_201_CREATED)
async def add_case(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: BenchmarkCaseAdd,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = BenchmarkService(db)
    try:
        return await service.add_case(bid, body.curated_item_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.delete("/{bid}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_case(
    pid: uuid.UUID,
    bid: uuid.UUID,
    case_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = BenchmarkService(db)
    if not await service.remove_case(bid, case_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准案例不存在")


@router.post("/{bid}/export", response_model=ExportResponse, status_code=status.HTTP_201_CREATED)
async def export_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: ExportRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    from app.workers.export_worker import run_export_benchmark

    # Verify benchmark exists
    service = BenchmarkService(db)
    benchmark = await service.get_benchmark(bid)
    if benchmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")

    # Create task for tracking
    redis = getattr(request.app.state, "redis", None)
    task_service = TaskService(db, redis)
    task = await task_service.create_task(
        project_id=pid,
        task_type="export",
        entity_type="benchmark",
        entity_id=bid,
        created_by=current_user.id,
    )

    background_tasks.add_task(
        run_export_benchmark,
        task_id=task.id,
        project_id=pid,
        benchmark_id=bid,
        export_profile_id=body.export_profile_id,
        created_by=current_user.id,
        db=db,
        redis=redis,
    )

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
