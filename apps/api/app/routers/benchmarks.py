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
    BenchmarkCaseAdd,
    BenchmarkCaseDetailResponse,
    BenchmarkCreate,
    BenchmarkDetailResponse,
    BenchmarkFinalizeRequest,
    BenchmarkResponse,
    BenchmarkUpdate,
    CuratedItemSummaryResponse,
)
from app.schemas.export import ExportRequest, ExportResponse
from app.services.benchmark_service import BenchmarkService
from app.services.composition_policy import CompositionGateError
from app.services.composition_service import CompositionService
from app.services.idempotency import idempotent_create_task
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/benchmarks", tags=["benchmarks"])


def _gate_409(exc: CompositionGateError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": exc.code, "message": str(exc)},
    )


def _benchmark_detail(benchmark, case_count: int) -> dict:
    base = BenchmarkResponse.model_validate(benchmark).model_dump()
    return BenchmarkDetailResponse(
        **base,
        case_count=case_count,
        composition_revision=benchmark.composition_revision,
        composition_sha256=benchmark.composition_sha256,
        composition_canonicalization_version=benchmark.composition_canonicalization_version,
        finalized_revision=benchmark.finalized_revision,
        finalized_sha256=benchmark.finalized_sha256,
        finalized_canonicalization_version=benchmark.finalized_canonicalization_version,
        finalized_by=benchmark.finalized_by,
        finalized_at=benchmark.finalized_at,
    ).model_dump()


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


@router.get("/{bid}", response_model=BenchmarkDetailResponse, operation_id="benchmark_get")
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
    service = BenchmarkService(db)
    case_count = await service.count_cases(bid)
    return _benchmark_detail(benchmark, case_count)


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
    response_model=PaginatedResponse[BenchmarkCaseDetailResponse],
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
    items, total = await service.list_cases(bid, page, page_size)
    composition_service = CompositionService(db)
    details = await composition_service.build_membership_details("benchmark", bid, items)
    return PaginatedResponse(items=details, total=total, page=page, page_size=page_size)


@router.get(
    "/{bid}/eligible-items",
    response_model=PaginatedResponse[CuratedItemSummaryResponse],
    operation_id="benchmark_list_eligible_items",
)
async def list_eligible_items(
    pid: uuid.UUID,
    bid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    query: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    composition_service = CompositionService(db)
    items, total = await composition_service.list_eligible_items(
        container_type="benchmark",
        container_id=bid,
        project_id=pid,
        query=query,
        page=page,
        page_size=page_size,
        require_supported=True,
    )
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{bid}/cases", response_model=BenchmarkCaseDetailResponse, status_code=status.HTTP_201_CREATED, operation_id="benchmark_add_case")
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
    composition_service = CompositionService(db)
    try:
        membership = await composition_service.add_membership(
            container_type="benchmark",
            container_id=bid,
            curated_item_id=body.curated_item_id,
            require_supported=True,
        )
    except CompositionGateError as exc:
        raise _gate_409(exc) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "COMPOSITION_MEMBER_EXISTS", "message": "同一 CuratedItem 已在容器中"},
        ) from exc
    details = await composition_service.build_membership_details("benchmark", bid, [membership])
    return details[0]


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
    composition_service = CompositionService(db)
    try:
        removed = await composition_service.remove_membership(
            container_type="benchmark", container_id=bid, membership_id=case_id
        )
    except CompositionGateError as exc:
        raise _gate_409(exc) from exc
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准案例不存在")


@router.post("/{bid}/finalize", response_model=BenchmarkDetailResponse, operation_id="benchmark_finalize")
async def finalize_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: BenchmarkFinalizeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.reviewer))],
):
    resolver = ProjectResourceResolver(db)
    benchmark = await resolver.benchmark(pid, bid)
    if benchmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    composition_service = CompositionService(db)
    try:
        finalized = await composition_service.finalize(
            container_type="benchmark",
            container_id=bid,
            expected_revision=body.expected_revision,
            expected_sha256=body.expected_sha256,
            reviewer_id=current_user.id,
        )
    except CompositionGateError as exc:
        raise _gate_409(exc) from exc
    case_count = await BenchmarkService(db).count_cases(bid)
    return _benchmark_detail(finalized, case_count)


@router.post("/{bid}/export", response_model=ExportResponse, status_code=status.HTTP_201_CREATED, operation_id="benchmark_export")
async def export_benchmark(
    pid: uuid.UUID,
    bid: uuid.UUID,
    body: ExportRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):

    # Verify benchmark exists and export profile belongs to the same project
    resolver = ProjectResourceResolver(db)
    if await resolver.benchmark(pid, bid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="基准集不存在")
    await resolver.ensure_in_project(pid, [(ExportProfile, body.export_profile_id)])

    # Create task for tracking
    redis = getattr(request.app.state, "redis", None)
    task_service = TaskService(db, redis)
    export_payload = {
        "benchmark_id": str(bid),
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
                project_id=pid, task_type="export", entity_type="benchmark", entity_id=bid,
                created_by=current_user.id, payload=export_payload, handler="export_benchmark",
                idempotency_key=key,
            ),
        )
    else:
        task = await task_service.create_task(
            project_id=pid, task_type="export", entity_type="benchmark", entity_id=bid,
            created_by=current_user.id, payload=export_payload, handler="export_benchmark",
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
