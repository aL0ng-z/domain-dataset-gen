import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.user import User
from app.schemas.task import TaskResponse
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/tasks", tags=["tasks"])


@router.get("/", response_model=PaginatedResponse[TaskResponse], operation_id="task_list")
async def list_tasks(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    task_type: str | None = None,
    task_status: str | None = Query(None, alias="status"),
):
    service = TaskService(db)
    tasks, total = await service.list_tasks(pid, page, page_size, task_type, task_status)
    return PaginatedResponse(items=tasks, total=total, page=page, page_size=page_size)


@router.get("/{tid}", response_model=TaskResponse, operation_id="task_get")
async def get_task(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    task = await resolver.task(pid, tid)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task


@router.post("/{tid}/cancel", response_model=TaskResponse, operation_id="task_cancel")
async def cancel_task(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.task(pid, tid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    service = TaskService(db)
    task = await service.cancel_task(tid)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task


@router.post("/{tid}/retry", response_model=TaskResponse, operation_id="task_retry")
async def retry_task(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    task = await resolver.task(pid, tid)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task.status != "failed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅失败任务可重试")
    service = TaskService(db)
    updated = await service.update_status(tid, "queued", progress=0)
    return updated
