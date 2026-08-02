"""ChunkSet 路由（T06 §5）：版本历史与冻结配置查询。

- ``GET /projects/{pid}/documents/{did}/chunk-sets``：分页版本历史（含统计/hash/是否 active）。
- ``GET /projects/{pid}/chunk-sets/{csid}``：单个集合冻结配置与汇总。
- 对象归属遵循 T02（ProjectResourceResolver 校验资源真实属于 pid）。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver
from app.database import get_db
from app.dependencies import require_project_member
from app.models.chunk_set import ChunkSet
from app.models.cleaned_document_version import CleanedDocumentVersion
from app.models.config import ChunkProfile
from app.models.user import User
from app.schemas.chunk_set import ChunkSetDetailResponse, ChunkSetListResponse, ChunkSetSummary
from domain.enums import UserRole

router = APIRouter(prefix="/api/projects/{pid}", tags=["chunk-sets"])


def _to_summary(cs: ChunkSet, *, is_active: bool, version_no: int | None = None,
                profile_name: str | None = None) -> ChunkSetSummary:
    return ChunkSetSummary(
        id=cs.id,
        document_id=cs.document_id,
        version=cs.version,
        is_legacy=cs.is_legacy,
        status=cs.status,
        cleaned_document_version_id=cs.cleaned_document_version_id,
        cleaned_document_version=version_no,
        chunk_profile_id=cs.chunk_profile_id,
        chunk_profile_name=profile_name,
        strategy=cs.strategy,
        config_json=cs.config_json,
        total_chunks=cs.total_chunks,
        total_tokens=cs.total_tokens,
        source_sha256=cs.source_sha256,
        output_sha256=cs.output_sha256,
        splitter_version=cs.splitter_version,
        error_message=cs.error_message,
        task_id=cs.task_id,
        completed_at=cs.completed_at,
        created_at=cs.created_at,
        is_active=is_active,
    )


@router.get("/documents/{did}/chunk-sets", response_model=ChunkSetListResponse, operation_id="document_list_chunk_sets")
async def list_chunk_sets(
    pid: uuid.UUID,
    did: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
):
    """分页返回文档的切分版本历史（新版本优先）。"""
    resolver = ProjectResourceResolver(db)
    doc = await resolver.document(pid, did)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    offset = (page - 1) * page_size
    base = select(ChunkSet).where(ChunkSet.document_id == did)
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(
        base.order_by(ChunkSet.version.desc()).offset(offset).limit(page_size)
    )
    sets = list(result.scalars().all())

    # 关联查询：cleaned version 号与 profile 名（非敏感展示字段）。
    version_nos: dict[uuid.UUID, int] = {}
    if sets:
        cids = [s.cleaned_document_version_id for s in sets if s.cleaned_document_version_id]
        if cids:
            vrows = (
                await db.execute(
                    select(CleanedDocumentVersion.id, CleanedDocumentVersion.version).where(
                        CleanedDocumentVersion.id.in_(cids)
                    )
                )
            ).all()
            version_nos = {r[0]: r[1] for r in vrows}
    profile_names: dict[uuid.UUID, str] = {}
    pids = [s.chunk_profile_id for s in sets if s.chunk_profile_id]
    if pids:
        prows = (
            await db.execute(select(ChunkProfile.id, ChunkProfile.name).where(ChunkProfile.id.in_(pids)))
        ).all()
        profile_names = {r[0]: r[1] for r in prows}

    items = [
        _to_summary(
            cs,
            is_active=doc.active_chunk_set_id == cs.id,
            version_no=version_nos.get(cs.cleaned_document_version_id),
            profile_name=profile_names.get(cs.chunk_profile_id),
        )
        for cs in sets
    ]
    return ChunkSetListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/chunk-sets/{csid}", response_model=ChunkSetDetailResponse, operation_id="chunk_set_get")
async def get_chunk_set(
    pid: uuid.UUID,
    csid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_project_member(UserRole.viewer))],
):
    """返回单个切分集合的冻结配置与汇总（对象归属遵循 T02）。"""
    resolver = ProjectResourceResolver(db)
    cs = await resolver.chunk_set(pid, csid)
    if cs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="切分集合不存在")
    doc = await resolver.document(pid, cs.document_id)
    return ChunkSetDetailResponse(
        id=cs.id,
        document_id=cs.document_id,
        version=cs.version,
        is_legacy=cs.is_legacy,
        status=cs.status,
        cleaned_document_version_id=cs.cleaned_document_version_id,
        chunk_profile_id=cs.chunk_profile_id,
        strategy=cs.strategy,
        config_json=cs.config_json,
        total_chunks=cs.total_chunks,
        total_tokens=cs.total_tokens,
        source_sha256=cs.source_sha256,
        output_sha256=cs.output_sha256,
        splitter_version=cs.splitter_version,
        error_message=cs.error_message,
        task_id=cs.task_id,
        completed_at=cs.completed_at,
        created_at=cs.created_at,
        is_active=doc.active_chunk_set_id == cs.id if doc else False,
    )
