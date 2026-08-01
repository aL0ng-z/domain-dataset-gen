import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_project_member
from app.models.config import ChunkProfile, ExportProfile, ModelConfig, ParserProfile, TaskPolicy
from app.models.user import User
from app.schemas.config import (
    ChunkProfileCreate,
    ChunkProfileResponse,
    ChunkProfileUpdate,
    ExportProfileCreate,
    ExportProfileResponse,
    ExportProfileUpdate,
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    ParserEndpointListResponse,
    ParserProfileCreate,
    ParserProfileResponse,
    ParserProfileUpdate,
    TaskPolicyCreate,
    TaskPolicyResponse,
    TaskPolicyUpdate,
)
from app.services.config_service import ConfigService, ParserProfileService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(tags=["config"])


def _make_config_router(
    prefix: str,
    model_class,
    create_schema,
    update_schema,
    response_schema,
    tag: str,
):
    """Factory to create CRUD router for a config profile type."""
    sub_router = APIRouter(prefix=prefix, tags=[tag])

    @sub_router.post("/", response_model=response_schema, status_code=status.HTTP_201_CREATED)
    async def create(
        pid: uuid.UUID,
        body: create_schema,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ConfigService(db, model_class)
        return await service.create(pid, **body.model_dump())

    @sub_router.get("/", response_model=PaginatedResponse[response_schema])
    async def list_all(
        pid: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        service = ConfigService(db, model_class)
        items, total = await service.list(pid, page, page_size)
        return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)

    @sub_router.get("/{config_id}", response_model=response_schema)
    async def get(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    ):
        service = ConfigService(db, model_class)
        obj = await service.get(config_id)
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        return obj

    @sub_router.patch("/{config_id}", response_model=response_schema)
    async def update(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        body: update_schema,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ConfigService(db, model_class)
        obj = await service.update(config_id, **body.model_dump(exclude_unset=True))
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        return obj

    @sub_router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ConfigService(db, model_class)
        if not await service.delete(config_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")

    @sub_router.post("/{config_id}/set-default", response_model=response_schema)
    async def set_default(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ConfigService(db, model_class)
        obj = await service.set_default(pid, config_id)
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        return obj

    return sub_router


# Create sub-routers for each config type
model_config_router = _make_config_router(
    "/api/projects/{pid}/model-configs", ModelConfig,
    ModelConfigCreate, ModelConfigUpdate, ModelConfigResponse, "model-configs",
)

parser_profile_router = _make_config_router(
    "/api/projects/{pid}/parser-profiles", ParserProfile,
    ParserProfileCreate, ParserProfileUpdate, ParserProfileResponse, "parser-profiles",
)


# ParserProfile 专用路由：使用 ParserProfileService（endpoint_ref 校验 + 白名单收紧），
# 并新增只读端点安全列表。
def _make_parser_profile_router() -> APIRouter:
    sub_router = APIRouter(prefix="/api/projects/{pid}/parser-profiles", tags=["parser-profiles"])

    @sub_router.get("/endpoints", response_model=ParserEndpointListResponse)
    async def list_endpoints(
        pid: uuid.UUID,
        _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    ):
        from app.security.registry import get_registry

        registry = get_registry()
        return ParserEndpointListResponse(items=registry.list_for_ui())

    @sub_router.post("/", response_model=ParserProfileResponse, status_code=status.HTTP_201_CREATED)
    async def create(
        pid: uuid.UUID,
        body: ParserProfileCreate,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ParserProfileService(db)
        try:
            return await service.create(pid, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @sub_router.get("/", response_model=PaginatedResponse[ParserProfileResponse])
    async def list_all(
        pid: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ):
        service = ParserProfileService(db)
        items, total = await service.list(pid, page, page_size)
        return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)

    @sub_router.get("/{config_id}", response_model=ParserProfileResponse)
    async def get(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    ):
        service = ParserProfileService(db)
        obj = await service.get(config_id)
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        return obj

    @sub_router.patch("/{config_id}", response_model=ParserProfileResponse)
    async def update(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        body: ParserProfileUpdate,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ParserProfileService(db)
        try:
            obj = await service.update(config_id, **body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        return obj

    @sub_router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ParserProfileService(db)
        if not await service.delete(config_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")

    @sub_router.post("/{config_id}/set-default", response_model=ParserProfileResponse)
    async def set_default(
        pid: uuid.UUID,
        config_id: uuid.UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        _: Annotated[User, Depends(require_project_member(UserRole.editor))],
    ):
        service = ParserProfileService(db)
        obj = await service.set_default(pid, config_id)
        if obj is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")
        return obj

    return sub_router


parser_profile_router = _make_parser_profile_router()

chunk_profile_router = _make_config_router(
    "/api/projects/{pid}/chunk-profiles", ChunkProfile,
    ChunkProfileCreate, ChunkProfileUpdate, ChunkProfileResponse, "chunk-profiles",
)

export_profile_router = _make_config_router(
    "/api/projects/{pid}/export-profiles", ExportProfile,
    ExportProfileCreate, ExportProfileUpdate, ExportProfileResponse, "export-profiles",
)

task_policy_router = _make_config_router(
    "/api/projects/{pid}/task-policies", TaskPolicy,
    TaskPolicyCreate, TaskPolicyUpdate, TaskPolicyResponse, "task-policies",
)


# ModelConfig has an extra test endpoint
@model_config_router.post("/{config_id}/test")
async def test_model_config(
    pid: uuid.UUID,
    config_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.editor))],
):
    service = ConfigService(db, ModelConfig)
    config = await service.get(config_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="配置不存在")

    from llm import LLMClient

    try:
        client = LLMClient(
            base_url=config.base_url,
            api_key=config.api_key_encrypted,  # In MVP, stored as plaintext
            model_name=config.model_name,
            temperature=0,
            max_tokens=20,
        )
        response = await client.chat_completion(
            [{"role": "user", "content": "Reply with exactly: 连接成功"}],
            max_retries=1,
        )
        return {"status": "success", "response": response.content.strip(), "latency_ms": response.latency_ms}
    except Exception as e:
        return {"status": "error", "error": str(e)}
