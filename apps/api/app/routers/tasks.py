import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.user import User
from app.schemas.task import TaskAttemptResponse, TaskCancelResponse, TaskResponse
from app.services.task_service import TaskService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/tasks", tags=["tasks"])


def _task_response(task) -> TaskResponse:
    return TaskResponse.from_task(task)


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
    items = [_task_response(t) for t in tasks]
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


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
    return _task_response(task)


@router.get("/{tid}/attempts", response_model=list[TaskAttemptResponse], operation_id="task_attempts")
async def list_task_attempts(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    """返回按 attempt_no 排序的审计列表；不返回 payload 中可能敏感的内部字段。"""
    resolver = ProjectResourceResolver(db)
    if await resolver.task(pid, tid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    service = TaskService(db)
    attempts = await service.list_attempts(tid)
    return [TaskAttemptResponse.from_attempt(a) for a in attempts]


@router.post("/{tid}/cancel", response_model=TaskCancelResponse, operation_id="task_cancel")
async def cancel_task(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    task = await resolver.task(pid, tid)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    service = TaskService(db)
    updated = await service.cancel_task(tid, current_user.id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return TaskCancelResponse(
        id=updated.id,
        status=updated.status,
        state_version=updated.state_version,
        completed_at=updated.completed_at,
    )


@router.post("/{tid}/retry", response_model=TaskResponse, status_code=status.HTTP_201_CREATED, operation_id="task_retry")
async def retry_task(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_project_member(UserRole.editor))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    resolver = ProjectResourceResolver(db)
    task = await resolver.task(pid, tid)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    if task.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": "仅失败任务可重试"},
        )
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "重试需要 Idempotency-Key"},
        )

    service = TaskService(db)
    new_task, newly_created = await service.retry_task(
        task, idempotency_key=idempotency_key, created_by=current_user.id
    )
    if new_task is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务重试创建失败")
    return _task_response(new_task)
