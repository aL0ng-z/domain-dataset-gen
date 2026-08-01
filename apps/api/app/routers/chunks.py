import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver, authorize_flat_resource
from app.database import get_db
from app.dependencies import get_current_user
from app.models.chunk import Chunk
from app.models.config import ModelConfig
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.schemas.chunk import ChunkResponse, ChunkUpdate, GenerateRequest
from app.schemas.task import TaskResponse
from app.services.chunk_service import ChunkService
from domain.enums import UserRole

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


@router.get("/{cid}", response_model=ChunkResponse)
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
    return chunk


@router.patch("/{cid}", response_model=ChunkResponse)
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
    service = ChunkService(db)
    chunk = await service.update_chunk(cid, **body.model_dump(exclude_unset=True))
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk不存在")
    return chunk


@router.post("/{cid}/generate", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def generate_from_chunk(
    cid: uuid.UUID,
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
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

    # Schedule background generation worker（独立会话，避免复用请求会话）。
    from app.database import async_session_factory
    from app.workers.generate_worker import run_generate_single

    async def _run():
        async with async_session_factory() as session:
            try:
                await run_generate_single(
                    task.id,
                    cid,
                    body.prompt_template_id,
                    body.model_config_id,
                    pid,
                    session,
                    redis=None,
                )
                await session.commit()
            except Exception:
                await session.rollback()

    background_tasks.add_task(_run)
    return task
