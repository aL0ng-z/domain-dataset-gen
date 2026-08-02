import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver, authorize_flat_resource
from app.database import get_db
from app.dependencies import get_current_user
from app.models.chunk import Chunk
from app.models.chunk_set import ChunkSet
from app.models.config import ModelConfig
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.schemas.chunk import ChunkResponse, ChunkUpdate, GenerateRequest
from app.schemas.task import TaskResponse
from app.services.chunk_service import ChunkService
from domain.enums import UserRole

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


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


@router.post("/{cid}/generate", response_model=TaskResponse, status_code=status.HTTP_201_CREATED, operation_id="chunk_generate")
async def generate_from_chunk(
    cid: uuid.UUID,
    body: GenerateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.chunk_project_id(cid), UserRole.editor
    )
    # 请求体引用的 prompt template 与 model config 必须属于同一项目（任务卡 §5.1）。
    await resolver.ensure_in_project(
        pid,
        [
            (Chunk, cid),
            (PromptTemplate, body.prompt_template_id),
            (ModelConfig, body.model_config_id),
        ],
    )

    service = ChunkService(db)
    try:
        task, gen_run = await service.create_generate_task(
            chunk_id=cid,
            prompt_template_id=body.prompt_template_id,
            model_config_id=body.model_config_id,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # 业务 job + Task 同一事务提交后由独立 runner 领取（不再调用 background_tasks）。
    await db.commit()
    return task
