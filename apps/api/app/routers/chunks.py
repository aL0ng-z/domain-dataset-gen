import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver, authorize_flat_resource
from app.database import get_db
from app.dependencies import get_current_user
from app.models.chunk_set import ChunkSet
from app.models.user import User
from app.schemas.chunk import ChunkResponse, ChunkUpdate
from app.schemas.generation import (
    GenerateAcceptedResponse,
    GenerateRequest,
)
from app.services.chunk_service import ChunkService
from app.services.generation_service import (
    GenerationConfigUnavailableError,
    GenerationInProgressError,
    GenerationOrchestrationService,
    GenerationSourceNotReadyError,
    PromptTemplateVersionMaterializeError,
)
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import ErrorResponse

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


def _generation_http_error(
    status_code: int, code: str, message: str, context: dict | None = None
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ErrorResponse(code=code, message=message, context=context).model_dump(),
    )


def _chunk_response(chunk) -> ChunkResponse:
    """构建带 chunk_set_id/chunk_set_version 的响应（T06 §5）。"""
    resp = ChunkResponse.model_validate(chunk)
    return resp


async def _load_set_version(db: AsyncSession, chunk_set_id: uuid.UUID) -> int | None:
    result = await db.execute(
        select(ChunkSet.version).where(ChunkSet.id == chunk_set_id)
    )
    return result.scalar_one_or_none()


@router.get("/{cid}", response_model=ChunkResponse, operation_id="chunk_get")
async def get_chunk(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.chunk_project_id(cid), UserRole.viewer
    )
    chunk = await resolver.chunk(pid, cid)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk不存在")
    resp = _chunk_response(chunk)
    resp.chunk_set_version = await _load_set_version(db, chunk.chunk_set_id)
    return resp


@router.patch("/{cid}", response_model=ChunkResponse, operation_id="chunk_update")
async def update_chunk(
    cid: uuid.UUID,
    body: ChunkUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.chunk_project_id(cid), UserRole.editor
    )
    chunk = await resolver.chunk(pid, cid)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk不存在")

    # T06 §5：已完成集合上的 PATCH 返回 409 CHUNK_SET_IMMUTABLE（切分版本不可变）。
    if chunk.chunk_set_id is not None:
        cs = (
            await db.execute(select(ChunkSet).where(ChunkSet.id == chunk.chunk_set_id))
        ).scalar_one_or_none()
        if cs is not None and cs.status in ("completed", "rejected"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "CHUNK_SET_IMMUTABLE", "message": "切分版本不可变，禁止原地编辑"},
            )

    service = ChunkService(db)
    chunk = await service.update_chunk(cid, **body.model_dump(exclude_unset=True))
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk不存在")
    resp = _chunk_response(chunk)
    resp.chunk_set_version = await _load_set_version(db, chunk.chunk_set_id)
    return resp


@router.post("/{cid}/generate", response_model=GenerateAcceptedResponse, status_code=status.HTTP_202_ACCEPTED, operation_id="chunk_generate")
async def generate_from_chunk(
    cid: uuid.UUID,
    body: GenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """单 Chunk 生成（T08 §5.1）：202 接收，服务端固定选择 [cid]。"""
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.chunk_project_id(cid), UserRole.editor
    )
    chunk = await resolver.chunk(pid, cid)
    if chunk is None:
        raise _generation_http_error(
            404, "GENERATION_SOURCE_NOT_FOUND", "Chunk 不存在", {"source_type": "Chunk"}
        )

    # scoped 加载请求体引用的模板/模型；不存在或不可见 -> 404。
    template = await resolver.prompt_template(pid, body.prompt_template_id)
    if template is None:
        raise _generation_http_error(
            404, "GENERATION_CONFIG_NOT_FOUND", "PromptTemplate 不存在", {"config_type": "prompt_template"}
        )
    model_config = await resolver.model_config(pid, body.model_config_id)
    if model_config is None:
        raise _generation_http_error(
            404, "GENERATION_CONFIG_NOT_FOUND", "ModelConfig 不存在", {"config_type": "model_config"}
        )

    task_service = TaskService(db)
    service = GenerationOrchestrationService(db, task_service)
    batch = None
    parent_task = None
    try:
        batch, parent_task, _runs = await service.create_single(
            project_id=pid,
            chunk_id=cid,
            document_id=chunk.document_id,
            prompt_template_id=body.prompt_template_id,
            model_config_id=body.model_config_id,
            created_by=current_user.id,
        )
    except GenerationSourceNotReadyError as e:
        raise _generation_http_error(
            409, "GENERATION_SOURCE_NOT_READY", str(e),
            {"document_id": str(chunk.document_id)},
        ) from e
    except GenerationConfigUnavailableError as e:
        raise _generation_http_error(
            409, "GENERATION_CONFIG_UNAVAILABLE", str(e),
            {"config_type": "model_config", "config_id": str(body.model_config_id)},
        ) from e
    except (PromptTemplateVersionMaterializeError, ValueError) as e:
        raise _generation_http_error(
            409, "GENERATION_CONFIG_UNAVAILABLE", str(e), {"config_type": "prompt_template"}
        ) from e
    except GenerationInProgressError as e:
        raise _generation_http_error(
            409, "GENERATION_IN_PROGRESS", str(e),
            {"generation_batch_id": str(e.existing_batch_id) if e.existing_batch_id else None},
        ) from e

    # 业务 Batch + Task 同一事务提交后由独立 runner 领取。
    await db.commit()
    return GenerateAcceptedResponse(
        task_id=parent_task.id,
        generation_batch_id=batch.id,
        status="queued",
    )
