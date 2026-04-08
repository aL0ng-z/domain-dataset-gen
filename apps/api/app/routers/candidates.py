import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.user import User
from app.schemas.candidate import (
    CandidateCommentCreate,
    CandidateCommentResponse,
    CandidateResponse,
    CandidateReview,
    CandidateUpdate,
)
from app.schemas.curated import CuratedItemResponse
from app.services.candidate_service import CandidateService
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


@router.get("", response_model=PaginatedResponse)
async def list_candidates(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    service = CandidateService(db)
    items, total = await service.list_by_project(project_id, status=status, page=page, page_size=page_size)
    return PaginatedResponse(items=[CandidateResponse.model_validate(i) for i in items], total=total, page=page, page_size=page_size)


@router.get("/{cid}", response_model=CandidateResponse)
async def get_candidate(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = CandidateService(db)
    candidate = await service.get(cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return candidate


@router.patch("/{cid}", response_model=CandidateResponse)
async def update_candidate(
    cid: uuid.UUID,
    body: CandidateUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = CandidateService(db)
    if body.content is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="内容不能为空")
    candidate = await service.update_content(cid, body.content)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return candidate


@router.post("/{cid}/review", response_model=CandidateResponse)
async def review_candidate(
    cid: uuid.UUID,
    body: CandidateReview,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = CandidateService(db)
    candidate = await service.review(
        candidate_id=cid,
        reviewer_id=current_user.id,
        verdict=body.verdict,
        evidence_spans=body.evidence_spans,
        reject_reason=body.reject_reason,
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return candidate


@router.post("/{cid}/comments", response_model=CandidateCommentResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    cid: uuid.UUID,
    body: CandidateCommentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    service = CandidateService(db)
    # Verify candidate exists
    candidate = await service.get(cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return await service.add_comment(cid, current_user.id, body.content)


@router.get("/{cid}/comments", response_model=list[CandidateCommentResponse])
async def list_comments(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    service = CandidateService(db)
    # Verify candidate exists
    candidate = await service.get(cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return await service.list_comments(cid)


@router.post("/{cid}/promote-to-curated", response_model=CuratedItemResponse, status_code=status.HTTP_201_CREATED)
async def promote_to_curated(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_role(UserRole.reviewer))],
):
    service = CandidateService(db)
    try:
        curated_item = await service.promote_to_curated(cid, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return curated_item
