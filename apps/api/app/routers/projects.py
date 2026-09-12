import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import effective_project_role
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.user import User
from app.schemas.project import (
    ProjectAccessResponse,
    ProjectCreate,
    ProjectMemberAdd,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectUpdate,
)
from app.services.project_service import ProjectService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, operation_id="project_create")
async def create_project(
    body: ProjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = ProjectService(db)
    return await service.create_project(body.name, body.description, current_user.id)


@router.get("/", response_model=PaginatedResponse[ProjectResponse], operation_id="project_list")
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    service = ProjectService(db)
    projects, total = await service.list_projects(current_user.id, current_user.role, page, page_size)
    return PaginatedResponse(items=projects, total=total, page=page, page_size=page_size)


@router.get("/{pid}", response_model=ProjectResponse, operation_id="project_get")
async def get_project(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await effective_project_role(db, pid, current_user)
    service = ProjectService(db)
    project = await service.get_project(pid)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


@router.get("/{pid}/access", response_model=ProjectAccessResponse, operation_id="project_get_access")
async def get_project_access(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return ProjectAccessResponse(effective_role=await effective_project_role(db, pid, current_user))


@router.patch("/{pid}", response_model=ProjectResponse, operation_id="project_update")
async def update_project(
    pid: uuid.UUID,
    body: ProjectUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = ProjectService(db)
    project = await service.update_project(pid, **body.model_dump(exclude_unset=True))
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT, operation_id="project_delete")
async def delete_project(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = ProjectService(db)
    if not await service.delete_project(pid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")


@router.post("/{pid}/members", response_model=ProjectMemberResponse, status_code=status.HTTP_201_CREATED, operation_id="project_add_member")
async def add_member(
    pid: uuid.UUID,
    body: ProjectMemberAdd,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = ProjectService(db)
    return await service.add_member(pid, body.user_id, body.role)


@router.get("/{pid}/members", response_model=list[ProjectMemberResponse], operation_id="project_list_members")
async def list_members(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await effective_project_role(db, pid, current_user)
    service = ProjectService(db)
    return await service.list_members(pid)


@router.delete("/{pid}/members/{uid}", status_code=status.HTTP_204_NO_CONTENT, operation_id="project_remove_member")
async def remove_member(
    pid: uuid.UUID,
    uid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    service = ProjectService(db)
    if not await service.remove_member(pid, uid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成员不存在")


@router.post("/{pid}/clone-config-from/{source_pid}", status_code=status.HTTP_201_CREATED, operation_id="project_clone_config")
async def clone_config(
    pid: uuid.UUID,
    source_pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_role(UserRole.admin))],
):
    """Clone all config profiles from source project to target project."""
    from typing import Any

    from pydantic import BaseModel
    from sqlalchemy import select

    from app.models.config import ChunkProfile, ExportProfile, ModelConfig, ParserProfile, TaskPolicy

    class CloneConfigResponse(BaseModel):
        cloned: dict[str, int]

    config_tables = [ModelConfig, ParserProfile, ChunkProfile, ExportProfile, TaskPolicy]
    cloned: dict[str, Any] = {}
    for model_cls in config_tables:
        result = await db.execute(select(model_cls).where(model_cls.project_id == source_pid))
        items = result.scalars().all()
        count = 0
        for item in items:
            data = {c.name: getattr(item, c.name) for c in item.__table__.columns if c.name not in ("id", "project_id", "created_at", "updated_at")}
            new_item = model_cls(project_id=pid, **data)
            db.add(new_item)
            count += 1
        cloned[model_cls.__tablename__] = count
    await db.flush()
    return CloneConfigResponse(cloned=cloned)
