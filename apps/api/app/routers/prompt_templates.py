import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.generation.output_validation import OutputSchemaError
from app.models.user import User
from app.schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdate,
    PromptTemplateVersionResponse,
    TestRunRequest,
    TestRunResponse,
)
from app.services.prompt_template_service import PromptTemplateService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/projects/{pid}/prompt-templates", tags=["prompt-templates"])


@router.post("/", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED, operation_id="prompt_template_create")
async def create_prompt_template(
    pid: uuid.UUID,
    body: PromptTemplateCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = PromptTemplateService(db)
    try:
        return await service.create(project_id=pid, **body.model_dump())
    except OutputSchemaError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/", response_model=PaginatedResponse[PromptTemplateResponse], operation_id="prompt_template_list")
async def list_prompt_templates(
    pid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    task_type: str | None = None,
):
    service = PromptTemplateService(db)
    templates, total = await service.list_templates(pid, page, page_size, task_type)
    return PaginatedResponse(items=templates, total=total, page=page, page_size=page_size)


@router.get("/{tid}", response_model=PromptTemplateResponse, operation_id="prompt_template_get")
async def get_prompt_template(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    template = await resolver.prompt_template(pid, tid)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")
    return template


@router.patch("/{tid}", response_model=PromptTemplateResponse, operation_id="prompt_template_update")
async def update_prompt_template(
    pid: uuid.UUID,
    tid: uuid.UUID,
    body: PromptTemplateUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.prompt_template(pid, tid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")
    service = PromptTemplateService(db)
    try:
        updated = await service.update(tid, **body.model_dump(exclude_unset=True))
    except OutputSchemaError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return updated


@router.post("/{tid}/duplicate", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED, operation_id="prompt_template_duplicate")
async def duplicate_prompt_template(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.prompt_template(pid, tid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")
    service = PromptTemplateService(db)
    new_template = await service.duplicate(tid)
    return new_template


@router.post("/{tid}/test-run", response_model=TestRunResponse, operation_id="prompt_template_test_run")
async def test_run_prompt_template(
    pid: uuid.UUID,
    tid: uuid.UUID,
    body: TestRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.prompt_template(pid, tid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")
    # 请求体引用的 chunk 与 model config 必须属于同一项目。
    from app.models.chunk import Chunk
    from app.models.config import ModelConfig

    await resolver.ensure_in_project(pid, [(Chunk, body.chunk_id)])
    await resolver.ensure_in_project(pid, [(ModelConfig, body.model_config_id)])
    service = PromptTemplateService(db)
    try:
        result = await service.test_run(tid, body.chunk_id, body.model_config_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return TestRunResponse(**result)


@router.get("/{tid}/versions", response_model=list[PromptTemplateVersionResponse], operation_id="prompt_template_list_versions")
async def list_prompt_template_versions(
    pid: uuid.UUID,
    tid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    resolver = ProjectResourceResolver(db)
    if await resolver.prompt_template(pid, tid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模板不存在")
    service = PromptTemplateService(db)
    return await service.list_versions(tid)
