import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.chunk import ChunkResponse, ChunkUpdate, GenerateRequest
from app.schemas.task import TaskResponse
from app.services.chunk_service import ChunkService
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


@router.get("/{cid}", response_model=ChunkResponse)
async def get_chunk(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = ChunkService(db)
    chunk = await service.get_chunk(cid)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk不存在")
    return chunk


@router.patch("/{cid}", response_model=ChunkResponse)
async def update_chunk(
    cid: uuid.UUID,
    body: ChunkUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
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
    service = ChunkService(db)
    try:
        task, gen_run = await service.create_generate_task(
            chunk_id=cid,
            prompt_template_id=body.prompt_template_id,
            model_config_id=body.model_config_id,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Schedule background generation worker
    from app.workers.generate_worker import run_generate

    background_tasks.add_task(run_generate, str(gen_run.id), str(task.id))
    return task
