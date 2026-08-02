"""GenerationBatch 查询与 retry 路由（T08 §5.3、§5.4）。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.generation import GenerationRun
from app.models.generation_batch import GenerationBatch
from app.models.user import User
from app.schemas.generation import (
    GenerateAcceptedResponse,
    GenerationBatchResponse,
    GenerationRunResponse,
)
from app.services.generation_retry_service import (
    GenerationProvenanceInvalidError,
    GenerationRetryExistsError,
    GenerationRetryNotRetryableError,
    GenerationRetryPlanner,
)
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import ErrorResponse, PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/generation-batches", tags=["generation-batches"])


def _gen_http_error(status_code: int, code: str, message: str, context: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorResponse(code=code, message=message, context=context).model_dump(),
    )


async def _scoped_batch(
    db: AsyncSession, pid: uuid.UUID, gbid: uuid.UUID
) -> GenerationBatch:
    resolver = ProjectResourceResolver(db)
    batch = await resolver.generation_batch(pid, gbid)
    if batch is None:
        raise _gen_http_error(
            404, "GENERATION_SOURCE_NOT_FOUND", "生成批次不存在", {"source_type": "GenerationBatch"}
        )
    return batch


def _batch_response(batch: GenerationBatch) -> GenerationBatchResponse:
    return GenerationBatchResponse(
        id=batch.id,
        document_id=batch.document_id,
        chunk_set_id=batch.chunk_set_id,
        model_config_id=batch.model_config_id,
        prompt_template_id=batch.prompt_template_id,
        selected_chunk_ids=batch.selected_chunk_ids or [],
        status=batch.status,
        total_chunks=batch.total_chunks,
        completed_chunks=batch.completed_chunks,
        summary_json=batch.summary_json,
        created_by=batch.created_by,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        completed_at=batch.completed_at,
        retry_of_generation_batch_id=batch.retry_of_generation_batch_id,
        is_legacy=batch.is_legacy,
        provenance_status=batch.provenance_status,
        provenance_error_code=batch.provenance_error_code,
        prompt_template_version_id=batch.prompt_template_version_id,
        prompt_template_sha256_prefix=batch.prompt_template_sha256[:16] if batch.prompt_template_sha256 else None,
        model_config_sha256_prefix=batch.model_config_sha256[:16] if batch.model_config_sha256 else None,
        renderer_version=batch.renderer_version,
    )


@router.get("/{gbid}", response_model=GenerationBatchResponse, operation_id="generation_batch_get")
async def get_generation_batch(
    pid: uuid.UUID,
    gbid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    batch = await _scoped_batch(db, pid, gbid)
    return _batch_response(batch)


@router.get("/{gbid}/runs", response_model=PaginatedResponse[GenerationRunResponse], operation_id="generation_batch_list_runs")
async def list_generation_runs(
    pid: uuid.UUID,
    gbid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    await _scoped_batch(db, pid, gbid)
    offset = (page - 1) * page_size
    count_result = await db.execute(
        select(func.count()).select_from(GenerationRun).where(GenerationRun.generation_batch_id == gbid)
    )
    total = count_result.scalar() or 0
    result = await db.execute(
        select(GenerationRun)
        .where(GenerationRun.generation_batch_id == gbid)
        .order_by(GenerationRun.created_at)
        .offset(offset)
        .limit(page_size)
    )
    items = [
        GenerationRunResponse(
            id=r.id,
            chunk_id=r.chunk_id,
            generation_batch_id=r.generation_batch_id,
            status=r.status,
            context_mode=r.context_mode,
            raw_output=r.raw_output,
            error_message=r.error_message,
            input_prompt=r.input_prompt,
            rendered_prompt_sha256=r.rendered_prompt_sha256,
            is_legacy=r.is_legacy,
            provenance_status=r.provenance_status,
            provenance_error_code=r.provenance_error_code,
            created_at=r.created_at,
            completed_at=r.completed_at,
        )
        for r in result.scalars().all()
    ]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("/{gbid}/retry", response_model=GenerateAcceptedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="generation_batch_retry")
async def retry_generation_batch(
    pid: uuid.UUID,
    gbid: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """对 failed/cancelled Batch 人工 retry：派生新 Task/Batch/Run，旧终态不变。"""
    batch = await _scoped_batch(db, pid, gbid)
    if batch.status not in ("failed", "cancelled"):
        raise _gen_http_error(
            409, "GENERATION_NOT_RETRYABLE", "仅 failed/cancelled 批次可重试",
            {"generation_batch_id": str(gbid)},
        )

    task_service = TaskService(db, request.app.state.redis)
    planner = GenerationRetryPlanner(db, task_service)
    try:
        new_batch, new_parent = await planner.plan_retry(
            source_batch=batch,
            project_id=pid,
            created_by=current_user.id,
            idempotency_key=idempotency_key,
        )
    except GenerationProvenanceInvalidError as e:
        raise _gen_http_error(
            409, "GENERATION_PROVENANCE_INVALID", str(e),
            {"generation_batch_id": str(gbid), "provenance_status": batch.provenance_status},
        ) from e
    except GenerationRetryNotRetryableError as e:
        raise _gen_http_error(
            409, "GENERATION_NOT_RETRYABLE", str(e),
            {"generation_batch_id": str(gbid)},
        ) from e
    except GenerationRetryExistsError as e:
        raise _gen_http_error(
            409, "GENERATION_RETRY_EXISTS", str(e),
            {"generation_batch_id": str(gbid)},
        ) from e

    await db.commit()
    return GenerateAcceptedResponse(
        task_id=new_parent.id,
        generation_batch_id=new_batch.id,
        status="queued",
    )
