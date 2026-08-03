import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import ProjectResourceResolver, authorize_flat_resource, check_project_member
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.candidate import (
    CandidateCommentCreate,
    CandidateCommentResponse,
    CandidateResponse,
    CandidateReview,
    CandidateUpdate,
)
from app.schemas.curated import CuratedItemResponse
from app.services.candidate_service import (
    CandidateAlreadyPromotedError,
    CandidateEvidenceRequiredError,
    CandidateService,
    CandidateStateConflictError,
    EvidenceValidationError,
)
from domain.enums import UserRole
from domain.schemas import PaginatedResponse

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


@router.get(
    "",
    response_model=PaginatedResponse[CandidateResponse],
    operation_id="candidate_list",
)
async def list_candidates(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    await check_project_member(db, project_id, current_user, UserRole.viewer)
    service = CandidateService(db)
    items, total = await service.list_by_project(project_id, status=status, page=page, page_size=page_size)
    return PaginatedResponse(items=[CandidateResponse.model_validate(i) for i in items], total=total, page=page, page_size=page_size)


@router.get("/{cid}", response_model=CandidateResponse, operation_id="candidate_get")
async def get_candidate(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.candidate_project_id(cid), UserRole.viewer
    )
    candidate = await resolver.candidate(pid, cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return candidate


@router.patch("/{cid}", response_model=CandidateResponse, operation_id="candidate_update")
async def update_candidate(
    cid: uuid.UUID,
    body: CandidateUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.candidate_project_id(cid), UserRole.editor
    )
    candidate = await resolver.candidate(pid, cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    if body.content is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="内容不能为空")
    service = CandidateService(db)
    try:
        candidate = await service.update_content(cid, body.content)
    except CandidateAlreadyPromotedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANDIDATE_ALREADY_PROMOTED", "message": str(e)},
        ) from e
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return candidate


@router.post("/{cid}/review", response_model=CandidateResponse, operation_id="candidate_review")
async def review_candidate(
    cid: uuid.UUID,
    body: CandidateReview,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.candidate_project_id(cid), UserRole.reviewer
    )
    candidate = await resolver.candidate(pid, cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    service = CandidateService(db)
    try:
        candidate = await service.review(
            candidate_id=cid,
            reviewer_id=current_user.id,
            verdict=body.verdict,
            evidence_spans=[s.model_dump() for s in body.evidence_spans] if body.evidence_spans else None,
            reject_reason=body.reject_reason,
        )
    except CandidateStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANDIDATE_REVIEW_STATE_CONFLICT", "message": str(e)},
        ) from e
    except CandidateAlreadyPromotedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANDIDATE_REVIEW_STATE_CONFLICT", "message": str(e)},
        ) from e
    except CandidateEvidenceRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANDIDATE_EVIDENCE_REQUIRED", "message": str(e)},
        ) from e
    except EvidenceValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": str(e)},
        ) from e
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    return candidate


@router.post("/{cid}/comments", response_model=CandidateCommentResponse, status_code=status.HTTP_201_CREATED, operation_id="candidate_add_comment")
async def add_comment(
    cid: uuid.UUID,
    body: CandidateCommentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.candidate_project_id(cid), UserRole.editor
    )
    candidate = await resolver.candidate(pid, cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    service = CandidateService(db)
    return await service.add_comment(cid, current_user.id, body.content)


@router.get("/{cid}/comments", response_model=list[CandidateCommentResponse], operation_id="candidate_list_comments")
async def list_comments(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.candidate_project_id(cid), UserRole.viewer
    )
    candidate = await resolver.candidate(pid, cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    service = CandidateService(db)
    return await service.list_comments(cid)


@router.post("/{cid}/promote-to-curated", response_model=CuratedItemResponse, status_code=status.HTTP_201_CREATED, operation_id="candidate_promote_to_curated")
async def promote_to_curated(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    resolver = ProjectResourceResolver(db)
    pid = await authorize_flat_resource(
        db, current_user, await resolver.candidate_project_id(cid), UserRole.reviewer
    )
    candidate = await resolver.candidate(pid, cid)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="候选项不存在")
    service = CandidateService(db)
    try:
        curated_item = await service.promote_to_curated(cid, current_user.id)
    except CandidateAlreadyPromotedError as e:
        # 409 + context 只返回同项目可见 item id（不创建第二份记录）。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CANDIDATE_ALREADY_PROMOTED",
                "message": str(e),
                "context": {"curated_item_id": str(e.item_id) if e.item_id else None},
            },
        ) from e
    except CandidateStateConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANDIDATE_REVIEW_STATE_CONFLICT", "message": str(e)},
        ) from e
    except CandidateEvidenceRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANDIDATE_EVIDENCE_REQUIRED", "message": str(e)},
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return curated_item
